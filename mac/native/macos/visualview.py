"""A call's visual in the mini window's card: its own WKWebView, walled off. The visual is a page the voice's
side copy wrote, so it gets nothing: a store that keeps nothing (non-persistent), its one document served by a
scheme of its own (standby-visual:, which serves that document and nothing else: no file:, no Standby server),
the document's policy letting nothing in or out (mac/visuals.py), every navigation after the first refused, no
windows of its own, and one way out, the "card" message: {sb: "size"} or {sb: "open", href} for an https link
clicked. Made for one visual and torn down when the card closes or shows another, so nothing carries over.
"""

import json

import AppKit
import Foundation
import WebKit

SCHEME = "standby-visual"
HOME = SCHEME + "://visual/"          # the one address it may load: its own document
CANCEL, ALLOW = 0, 1                  # WKNavigationActionPolicy


def may_load(url, loaded):
    """A navigation in the visual's view: only its own document, once."""
    return not loaded and str(url or "") == HOME


def message(body):
    """The visual's message as the card takes it: {"sb": "size", want, w, h, need}, {"sb": "open", href} (https), or
    {"sb": "chose", label} (a choice clicked, 80 characters at most), else None."""
    if not isinstance(body, dict):
        return None
    if body.get("sb") == "size":
        try:
            return {"sb": "size", "want": str(body.get("want") or "medium")[:12],
                    "w": max(0, min(4000, int(body.get("w") or 0))), "h": max(0, min(4000, int(body.get("h") or 0))),
                    "need": max(0, min(4000, int(body.get("need") or 0)))}
        except (TypeError, ValueError):
            return None
    label = body.get("label")
    if body.get("sb") == "chose" and isinstance(label, str):
        label = " ".join(label.split())[:80]
        return {"sb": "chose", "label": label} if label else None
    href = body.get("href")
    if body.get("sb") == "open" and isinstance(href, str) and href.lower().startswith("https://") and len(href) < 2048 \
            and not any(c.isspace() for c in href):
        return {"sb": "open", "href": href}
    return None


def _plain(body):
    data, _err = Foundation.NSJSONSerialization.dataWithJSONObject_options_error_(body, 0, None)
    if data is None:
        return None
    try:
        return json.loads(bytes(data).decode("utf-8"))
    except ValueError:
        return None


class _Scheme(AppKit.NSObject):
    """standby-visual: serves the one document, with its policy as a header too."""

    def webView_startURLSchemeTask_(self, web, task):
        url = task.request().URL()
        if str(url.absoluteString()) != HOME or self.owner is None:
            task.didFailWithError_(Foundation.NSError.errorWithDomain_code_userInfo_("standby-visual", 404, None))
            return
        data = self.owner.doc.encode("utf-8")
        headers = {"Content-Type": "text/html; charset=utf-8", "Content-Security-Policy": self.owner.csp,
                   "Cache-Control": "no-store"}
        response = Foundation.NSHTTPURLResponse.alloc().initWithURL_statusCode_HTTPVersion_headerFields_(url, 200, "HTTP/1.1", headers)
        task.didReceiveResponse_(response)
        task.didReceiveData_(Foundation.NSData.dataWithBytes_length_(data, len(data)))
        task.didFinish()

    def webView_stopURLSchemeTask_(self, web, task):
        pass


class _Guard(AppKit.NSObject):
    """Navigation (only its own document, once), windows (none), and its one message channel."""

    def webView_decidePolicyForNavigationAction_decisionHandler_(self, web, action, handler):
        owner = self.owner
        ok = owner is not None and may_load(action.request().URL().absoluteString(), owner.loaded) \
            and action.targetFrame() is not None and action.targetFrame().isMainFrame()
        if ok:
            owner.loaded = True
        handler(ALLOW if ok else CANCEL)

    def webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(self, web, config, action, features):
        return None

    def userContentController_didReceiveScriptMessage_(self, controller, msg):
        owner = self.owner
        info = msg.frameInfo()
        if owner is None or info is None or not info.isMainFrame():
            return
        m = message(_plain(msg.body()))
        if m is not None:
            owner.on_message(m)


class _Web(WebKit.WKWebView):
    """Takes the first click: the card's panel is never key (a plain WKWebView spends it on focusing)."""

    def acceptsFirstMouse_(self, event):
        return True


class VisualView(object):
    """One visual's view, at `rect` in its card's content view. on_message(m) gets message()'s dicts."""

    def __init__(self, doc, csp, rect, on_message):
        self.doc, self.csp, self.on_message, self.loaded = doc, csp, on_message, False
        config = WebKit.WKWebViewConfiguration.alloc().init()
        config.setWebsiteDataStore_(WebKit.WKWebsiteDataStore.nonPersistentDataStore())
        self._scheme = _Scheme.alloc().init()
        self._scheme.owner = self
        config.setURLSchemeHandler_forURLScheme_(self._scheme, SCHEME)
        self._guard = _Guard.alloc().init()
        self._guard.owner = self
        config.userContentController().addScriptMessageHandler_name_(self._guard, "card")
        prefs = config.preferences()
        prefs.setJavaScriptCanOpenWindowsAutomatically_(False)
        for key in ("allowFileAccessFromFileURLs", "javaEnabled", "plugInsEnabled"):
            try:
                prefs.setValue_forKey_(False, key)
            except Exception:
                pass
        web = _Web.alloc().initWithFrame_configuration_(Foundation.NSMakeRect(*rect), config)
        web.setValue_forKey_(False, "drawsBackground")
        web.setWantsLayer_(True)                     # its content kept inside the card's round corners
        web.layer().setCornerRadius_(16)
        web.layer().setMasksToBounds_(True)
        web.setNavigationDelegate_(self._guard)
        web.setUIDelegate_(self._guard)
        web.setAllowsBackForwardNavigationGestures_(False)
        web.setAllowsLinkPreview_(False)
        self.web = web
        web.loadRequest_(Foundation.NSURLRequest.requestWithURL_(Foundation.NSURL.URLWithString_(HOME)))

    def place(self, rect):
        self.web.setFrame_(Foundation.NSMakeRect(*rect))

    def close(self):
        """Gone for good: off the screen, its page stopped, its channel and its delegates let go."""
        web, self.web = self.web, None
        if web is None:
            return
        web.stopLoading()
        web.setNavigationDelegate_(None)
        web.setUIDelegate_(None)
        web.configuration().userContentController().removeScriptMessageHandlerForName_("card")
        web.removeFromSuperview()
        self._guard.owner = self._scheme.owner = None
        self.on_message = lambda m: None

