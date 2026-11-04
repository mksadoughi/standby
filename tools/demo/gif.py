#!/usr/bin/env python3
"""An MP4 to a looping GIF, with macOS's own AVFoundation and ImageIO (nothing installed), for places that want a GIF.

    ~/.standby/venv/bin/python tools/demo/gif.py IN.mp4 OUT.gif [--width 480] [--fps 15]
"""
import argparse
import sys

import AVFoundation as AV
import CoreMedia as CM
import Quartz as Q
from Foundation import NSURL


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--width", type=int, default=480)
    ap.add_argument("--fps", type=float, default=15)
    a = ap.parse_args()
    asset = AV.AVURLAsset.URLAssetWithURL_options_(NSURL.fileURLWithPath_(a.src), None)
    dur = CM.CMTimeGetSeconds(asset.duration())
    gen = AV.AVAssetImageGenerator.assetImageGeneratorWithAsset_(asset)
    gen.setRequestedTimeToleranceBefore_(CM.kCMTimeZero)
    gen.setRequestedTimeToleranceAfter_(CM.kCMTimeZero)
    gen.setMaximumSize_((a.width, a.width * 4))
    n = int(dur * a.fps)
    out = Q.CGImageDestinationCreateWithURL(NSURL.fileURLWithPath_(a.dst), "com.compuserve.gif", n, None)
    Q.CGImageDestinationSetProperties(out, {Q.kCGImagePropertyGIFDictionary: {Q.kCGImagePropertyGIFLoopCount: 0}})
    frame = {Q.kCGImagePropertyGIFDictionary: {Q.kCGImagePropertyGIFDelayTime: 1 / a.fps, Q.kCGImagePropertyGIFUnclampedDelayTime: 1 / a.fps}}
    for i in range(n):
        got = gen.copyCGImageAtTime_actualTime_error_(CM.CMTimeMakeWithSeconds(i / a.fps, 600), None, None)
        img, err = got[0], got[-1]
        if img is None:
            sys.exit("no frame at %.2f s: %s" % (i / a.fps, err))
        Q.CGImageDestinationAddImage(out, img, frame)
    if not Q.CGImageDestinationFinalize(out):
        sys.exit("the GIF wasn't written")
    print("ok   %s: %d frames at %g fps, %d px wide" % (a.dst, n, a.fps, a.width))


if __name__ == "__main__":
    main()
