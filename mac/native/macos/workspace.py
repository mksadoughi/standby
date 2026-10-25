"""native/workspace.py on a Mac: NSWorkspace."""


def app_icon(bundle, size):
    """NSWorkspace and NSImage are safe off the main thread, where this runs."""
    import AppKit
    import Quartz
    from Foundation import NSMutableData
    ws = AppKit.NSWorkspace.sharedWorkspace()
    url = ws.URLForApplicationWithBundleIdentifier_(bundle)
    if url is None:
        return None
    # pyobjc takes only None for the proposed rect (an in/out pointer), and hands back
    # (the image, the rect it used): the largest one, drawn down to `size` below.
    got = ws.iconForFile_(url.path()).CGImageForProposedRect_context_hints_(None, None, None)
    cg = got[0] if isinstance(got, tuple) else got
    if cg is None:
        return None
    ctx = Quartz.CGBitmapContextCreate(None, size, size, 8, 0, Quartz.CGColorSpaceCreateDeviceRGB(),
                                       Quartz.kCGImageAlphaPremultipliedLast)
    Quartz.CGContextSetInterpolationQuality(ctx, Quartz.kCGInterpolationHigh)
    Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(0, 0, size, size), cg)
    data = NSMutableData.data()
    dest = Quartz.CGImageDestinationCreateWithData(data, "public.png", 1, None)
    Quartz.CGImageDestinationAddImage(dest, Quartz.CGBitmapContextCreateImage(ctx), None)
    return bytes(data) if Quartz.CGImageDestinationFinalize(dest) else None
