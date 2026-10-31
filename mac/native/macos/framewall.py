"""The main window's wall around the frames in it (a call's visuals, each a sandboxed <iframe>): pywebview
hands its native bridge to every frame of the page, and lets any frame load any address. A visual is a page
the voice's side copy wrote, so it must reach neither: messages to Python come from the main frame only, and
a frame may hold its own document (srcdoc) and nothing else, so a visual can never load a web page.
install() before pywebview makes its first window."""

ALLOWED_IN_FRAMES = ("about:srcdoc", "about:blank")
CANCEL = 0                                    # WKNavigationActionPolicyCancel


def frame_may_load(url):
    """A navigation in a frame that isn't the page's own: only its own document."""
    return str(url or "") in ALLOWED_IN_FRAMES


def _from_main(message):
    try:
        info = message.frameInfo()
        return info is not None and bool(info.isMainFrame())
    except Exception:
        return False


def install():
    """Wraps pywebview's bridge and navigation delegate (macOS). Once; never fails the app."""
    from webview.platforms import cocoa
    view = cocoa.BrowserView
    if getattr(view, "_sb_walled", False):
        return
    bridge, delegate = view.JSBridge, view.BrowserDelegate

    class SBWalledBridge(bridge):
        def userContentController_didReceiveScriptMessage_(self, controller, message):
            if _from_main(message):
                bridge.userContentController_didReceiveScriptMessage_(self, controller, message)

    class SBWalledDelegate(delegate):
        def userContentController_didReceiveScriptMessage_(self, controller, message):
            if _from_main(message):
                delegate.userContentController_didReceiveScriptMessage_(self, controller, message)

        def webView_decidePolicyForNavigationAction_decisionHandler_(self, webview, action, handler):
            target = action.targetFrame()
            if target is not None and not target.isMainFrame() and not frame_may_load(action.request().URL().absoluteString()):
                handler(CANCEL)
                return
            delegate.webView_decidePolicyForNavigationAction_decisionHandler_(self, webview, action, handler)

    view.JSBridge, view.BrowserDelegate = SBWalledBridge, SBWalledDelegate
    view._sb_walled = True
