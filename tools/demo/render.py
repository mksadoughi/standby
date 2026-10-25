#!/usr/bin/env python3
"""A demo scene to an MP4: every frame drawn by one headless Chrome, encoded by macOS's own AVFoundation.

    ~/.standby/venv/bin/python tools/demo/render.py SCENE.html OUT.mp4 [--dark] [--fps 30] [--size 960x540]
                                                   [--scale 2] [--poster T] [--stills T,T,...] [--no-video] [--query v=b]

A scene is a page with window.render(t): the whole frame as a function of time, so every run makes the
same frames, whatever the Mac's load. Opened with ?render, it waits to be stepped. Nothing is installed:
Chrome is the one ui_preview.py drives, and the encoder is PyObjC's AVFoundation in ~/.standby/venv.
--poster T writes OUT's .jpg at time T; --stills writes a few frames beside it, to look at without playing.

The site's film, from the repository's root, with its soundtrack in Standby's own voices:
    voiceover.py /tmp/story.wav 65
    render.py tools/demo/story.html docs/video/story.mp4 --audio /tmp/story.wav --poster 6.4
"""
import argparse
import base64
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ui_preview as up  # noqa: E402

import AVFoundation as AV  # noqa: E402
import CoreMedia as CM  # noqa: E402
import Quartz as Q  # noqa: E402
from Foundation import NSURL, NSData  # noqa: E402


class Chrome(up.Chrome):
    """ui_preview's Chrome, with a profile of its own, so a render beside a preview never shares one."""

    def __init__(self):
        self.profile = tempfile.mkdtemp(prefix="sbdemo-chrome-")
        to_chrome, self._w = os.pipe()
        self._r, from_chrome = os.pipe()

        def wire():
            hi = [fcntl.fcntl(fd, fcntl.F_DUPFD, 10) for fd in (to_chrome, from_chrome)]
            os.dup2(hi[0], 3)
            os.dup2(hi[1], 4)
        self.proc = subprocess.Popen(
            [up.CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
             "--user-data-dir=" + self.profile, "--remote-debugging-pipe", "about:blank"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=False, preexec_fn=wire)
        os.close(to_chrome)
        os.close(from_chrome)
        self._buf, self._id, self.events = b"", 0, []
        try:
            page = next(t for t in self.call("Target.getTargets")["targetInfos"] if t["type"] == "page")
            self.session = self.call("Target.attachToTarget", targetId=page["targetId"], flatten=True)["sessionId"]
            self.call("Page.enable", session=True)
        except BaseException:
            # it didn't answer (a Mac under heavy load): nothing will close it later, so close it now
            self.proc.kill()
            self.proc.wait()
            shutil.rmtree(self.profile, ignore_errors=True)
            raise

    def close(self):
        super().close()
        shutil.rmtree(self.profile, ignore_errors=True)


class Encoder:
    """H.264 in an MP4, from PNG frames, tagged sRGB-ish BT.709 so browsers show the page's own colours."""

    def __init__(self, out, w, h, fps, bitrate):
        self.w, self.h, self.fps, self.n = w, h, fps, 0
        out.unlink(missing_ok=True)                       # AVAssetWriter refuses a file that's already there
        self.writer, err = AV.AVAssetWriter.alloc().initWithURL_fileType_error_(NSURL.fileURLWithPath_(str(out)),
                                                                                AV.AVFileTypeMPEG4, None)
        if not self.writer:
            raise RuntimeError("AVAssetWriter: %s" % err)
        settings = {AV.AVVideoCodecKey: AV.AVVideoCodecTypeH264, AV.AVVideoWidthKey: w, AV.AVVideoHeightKey: h,
                    AV.AVVideoCompressionPropertiesKey: {AV.AVVideoAverageBitRateKey: bitrate,
                                                         AV.AVVideoProfileLevelKey: AV.AVVideoProfileLevelH264HighAutoLevel,
                                                         AV.AVVideoMaxKeyFrameIntervalKey: fps * 2},
                    AV.AVVideoColorPropertiesKey: {AV.AVVideoColorPrimariesKey: AV.AVVideoColorPrimaries_ITU_R_709_2,
                                                   AV.AVVideoTransferFunctionKey: AV.AVVideoTransferFunction_ITU_R_709_2,
                                                   AV.AVVideoYCbCrMatrixKey: AV.AVVideoYCbCrMatrix_ITU_R_709_2}}
        self.input = AV.AVAssetWriterInput.assetWriterInputWithMediaType_outputSettings_(AV.AVMediaTypeVideo, settings)
        self.input.setExpectsMediaDataInRealTime_(False)
        self.adaptor = AV.AVAssetWriterInputPixelBufferAdaptor.assetWriterInputPixelBufferAdaptorWithAssetWriterInput_sourcePixelBufferAttributes_(
            self.input, {Q.kCVPixelBufferPixelFormatTypeKey: Q.kCVPixelFormatType_32BGRA,
                         Q.kCVPixelBufferWidthKey: w, Q.kCVPixelBufferHeightKey: h})
        self.writer.addInput_(self.input)
        self.writer.startWriting()
        self.writer.startSessionAtSourceTime_(CM.CMTimeMake(0, fps))
        self.space = Q.CGColorSpaceCreateWithName(Q.kCGColorSpaceSRGB)

    def add(self, png):
        src = Q.CGImageSourceCreateWithData(NSData.dataWithBytes_length_(png, len(png)), None)
        img = Q.CGImageSourceCreateImageAtIndex(src, 0, None)
        ctx = Q.CGBitmapContextCreate(None, self.w, self.h, 8, self.w * 4, self.space,
                                      Q.kCGImageAlphaPremultipliedFirst | Q.kCGBitmapByteOrder32Little)
        Q.CGContextDrawImage(ctx, Q.CGRectMake(0, 0, self.w, self.h), img)
        # The pool's buffers come back without memory through PyObjC, so each frame gets its own.
        err, pb = Q.CVPixelBufferCreate(None, self.w, self.h, Q.kCVPixelFormatType_32BGRA,
                                        {Q.kCVPixelBufferIOSurfacePropertiesKey: {}}, None)
        Q.CVPixelBufferLockBaseAddress(pb, 0)
        bpr, row = Q.CVPixelBufferGetBytesPerRow(pb), self.w * 4
        dst = Q.CVPixelBufferGetBaseAddress(pb).as_buffer(bpr * self.h)
        data = Q.CGBitmapContextGetData(ctx).as_buffer(row * self.h)
        if bpr == row:
            dst[:] = data
        else:
            for y in range(self.h):
                dst[y * bpr:y * bpr + row] = data[y * row:(y + 1) * row]
        Q.CVPixelBufferUnlockBaseAddress(pb, 0)
        while not self.input.isReadyForMoreMediaData():
            time.sleep(0.002)
        if not self.adaptor.appendPixelBuffer_withPresentationTime_(pb, CM.CMTimeMake(self.n, self.fps)):
            raise RuntimeError("frame %d: %s" % (self.n, self.writer.error()))
        self.n += 1

    def close(self):
        self.input.markAsFinished()
        done = []
        self.writer.finishWritingWithCompletionHandler_(lambda: done.append(1))
        for _ in range(600):
            if done:
                break
            time.sleep(0.05)
        if self.writer.status() != AV.AVAssetWriterStatusCompleted:
            raise RuntimeError("the video wasn't finished: %s" % self.writer.error())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scene")
    ap.add_argument("out")
    ap.add_argument("--dark", action="store_true")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--size", default="960x540")
    ap.add_argument("--scale", type=float, default=2)
    ap.add_argument("--bitrate", type=int, default=4_000_000)
    ap.add_argument("--poster", type=float)
    ap.add_argument("--stills", default="")
    ap.add_argument("--no-video", action="store_true", help="only the poster and the stills, to check a scene quickly")
    ap.add_argument("--query", default="", help="more of the scene's address, e.g. v=b for one of its versions")
    ap.add_argument("--job", action="append", default=[], help="another version in the same Chrome: QUERY=OUT, e.g. v=b=/tmp/b.mp4")
    ap.add_argument("--audio", help="a soundtrack (WAV) to put under the video, e.g. from tools/demo/voiceover.py")
    a = ap.parse_args()
    w, h = (int(x) for x in a.size.split("x"))
    # More versions of one scene in the same Chrome: --job QUERY=OUT, as many as you like.
    jobs = [(a.query, a.out)] + [tuple(j.rsplit("=", 1)) for j in a.job]
    chrome = Chrome()
    try:
        for query, dest in jobs:
            render_one(chrome, a, w, h, query, Path(dest).resolve())
    finally:
        chrome.close()


def render_one(chrome, a, w, h, query, out):
    out.parent.mkdir(parents=True, exist_ok=True)
    chrome.shoot(Path(a.scene).resolve().as_uri() + "?render" + ("&" + query if query else ""), out.with_suffix(".jpg"), a.dark,
                 size=(w, h), scale=a.scale, settle=1.0)
    dur = chrome.call("Runtime.evaluate", session=True, returnByValue=True, expression="DUR")["result"]["value"]

    def at(t, fmt="png"):
        # Draw time t, wait for the page to paint it twice over, then take it.
        chrome.call("Runtime.evaluate", session=True, awaitPromise=True, expression=(
            "render(%s); new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))" % json.dumps(t)))
        shot = {"format": fmt} if fmt == "png" else {"format": "jpeg", "quality": 88}
        return base64.b64decode(chrome.call("Page.captureScreenshot", session=True, **shot)["data"])

    frames = int(round(dur * a.fps))
    if not a.no_video:
        enc = Encoder(out, int(w * a.scale), int(h * a.scale), a.fps, a.bitrate)
        for i in range(frames):
            enc.add(at(i / a.fps))
            if i % a.fps == 0:
                print("  %4.1f s" % (i / a.fps), flush=True)
        enc.close()
    if a.poster is not None:
        out.with_suffix(".jpg").write_bytes(at(a.poster, "jpeg"))
    for t in filter(None, a.stills.split(",")):
        (out.parent / ("%s-%05.2f.jpg" % (out.stem, float(t)))).write_bytes(at(float(t), "jpeg"))
    if not a.no_video and a.audio:
        with_sound(out, Path(a.audio).resolve())
    if not a.no_video:
        print("ok   %s: %d frames, %.1f s, %d KB" % (out, frames, frames / a.fps, out.stat().st_size // 1024))


def _export(asset, preset, dest, kind):
    """One AVAssetExportSession, run to the end."""
    dest.unlink(missing_ok=True)
    ex = AV.AVAssetExportSession.alloc().initWithAsset_presetName_(asset, preset)
    ex.setOutputURL_(NSURL.fileURLWithPath_(str(dest)))
    ex.setOutputFileType_(kind)
    done = []
    ex.exportAsynchronouslyWithCompletionHandler_(lambda: done.append(1))
    while not done:
        time.sleep(.05)
    if ex.status() != AV.AVAssetExportSessionStatusCompleted:
        raise RuntimeError("export: %s" % ex.error())


def with_sound(video, wav):
    """The video's frames as they are, with the soundtrack under them as AAC: the WAV becomes an M4A, then both go
    into one MP4 without re-encoding the video."""
    m4a = video.with_suffix(".sound.m4a")
    _export(AV.AVURLAsset.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(wav)), None), AV.AVAssetExportPresetAppleM4A, m4a, AV.AVFileTypeAppleM4A)
    v = AV.AVURLAsset.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(video)), None)
    s = AV.AVURLAsset.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(m4a)), None)
    comp = AV.AVMutableComposition.composition()
    span = CM.CMTimeRangeMake(CM.kCMTimeZero, v.duration())
    for asset, kind in ((v, AV.AVMediaTypeVideo), (s, AV.AVMediaTypeAudio)):
        src = asset.tracksWithMediaType_(kind)[0]
        dst = comp.addMutableTrackWithMediaType_preferredTrackID_(kind, 0)
        rng = span if kind == AV.AVMediaTypeVideo else CM.CMTimeRangeMake(CM.kCMTimeZero, CM.CMTimeMinimum(asset.duration(), v.duration()))
        ok, err = dst.insertTimeRange_ofTrack_atTime_error_(rng, src, CM.kCMTimeZero, None)
        if not ok:
            raise RuntimeError("soundtrack: %s" % err)
    both = video.with_suffix(".both.mp4")
    _export(comp, AV.AVAssetExportPresetPassthrough, both, AV.AVFileTypeMPEG4)
    both.replace(video)
    m4a.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
