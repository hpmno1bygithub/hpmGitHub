# -*- coding: utf-8 -*-
"""FIX94 offline single-canvas map editor host.

The native view count is fixed (root + WebView), independent of map size.
Pan/pinch/render stay inside Canvas. Python sees only bounded RPC requests.
Never invoke WebView.evaluate_js from a native UI callback: Pyto's synchronous
wrapper waits for WebKit's main-thread completion and could deadlock there.
"""
import json
import os
import pkgutil
import queue
import threading
import traceback
from collections import OrderedDict
import pyto_ui as ui
from ios_host.colors import color
from ios_host.orientation import lock_landscape, reassert_landscape
try:
    from mainthread import mainthread
except ImportError:
    def mainthread(f): return f
try: PRESENT_FULLSCREEN=ui.PresentationMode.FULLSCREEN
except AttributeError: PRESENT_FULLSCREEN=ui.PRESENTATION_MODE_FULLSCREEN


def editor_html():
    def resource(name):
        data=pkgutil.get_data('map_editor',name)
        if data is None:raise RuntimeError('編輯器資源缺少：'+name+'；請完整解壓 FIX94')
        return data.decode('utf-8')
    return resource('editor.html').replace('/*EDITOR_CSS*/',resource('editor.css')).replace('/*EDITOR_JS*/',resource('editor.js'))

class MapEditorUI:
    def __init__(self,project_root):
        self.project_root=os.path.abspath(project_root)
        self._closed=False;self._next_action=None;self._generation=0
        self._queue=queue.Queue(maxsize=24);self._replies=OrderedDict();self._session=None
        self._replies_bytes=0;self._native_pending=set();self._worker=None;self._close_completed=threading.Event()
        self.root=ui.View();self.root.background_color=color('#101925')
        lock_landscape(self.root)
        self.root.name='FIX94 地圖工作室'
        self.web=ui.WebView();self.web.background_color=color('#101925')
        self.web.did_receive_message=self._received
        self.web.did_fail_loading=self._load_failed
        self.web.register_message_handler('editor')
        self.root.add_subview(self.web)
        self.root.layout=self._layout
        self.root.did_disappear=self._did_disappear
        self._layout(self.root)
        self._worker=threading.Thread(target=self._work,name='MapEditor91Worker',daemon=True)
        self._worker.start()
        self.web.load_html(editor_html())

    def _layout(self,view):
        if self._closed:return
        try: reassert_landscape(view)
        except Exception:pass
        self.web.frame=(0,0,max(1,float(view.width)),max(1,float(view.height)))

    def _load_failed(self,web,error):
        print('FIX94 WebView 載入失敗:',error)

    def _did_disappear(self,*args):
        self._closed=True
        try:self._queue.put_nowait(None)
        except queue.Full:pass

    def _received(self,web,name,content):
        # Do not touch document, files, image decoders or JS evaluation here.
        if self._closed or name!='editor':return
        try:
            if isinstance(content,str):
                if len(content)>1024*1024:raise ValueError('指令過大')
                packet=json.loads(content)
            elif isinstance(content,dict):packet=content
            else:packet=json.loads(str(content))
            if not isinstance(packet,dict) or not isinstance(packet.get('id'),str):return
            if len(packet['id'])>80:return
            self._queue.put_nowait(packet)
        except (ValueError,TypeError,queue.Full) as exc:
            # JS will retry the SAME request ID, and worker replay prevents
            # double execution. No second callback/evaluation thread is spawned.
            print('FIX94 輸入佇列:',exc)

    def _send(self,packet):
        if self._closed:return
        payload=json.dumps(packet,ensure_ascii=True,separators=(',',':'))
        try:self.web.evaluate_js('window.EditorRPC && window.EditorRPC.resolve('+payload+'); true;')
        except Exception as exc:print('FIX94 回覆暫未送達（可重播）:',str(exc)[:240])

    def _work(self):
        # Session and every mutation are owned by this one worker.
        try:
            from map_editor.session import EditorSession
            self._session=EditorSession(self.project_root)
        except Exception as exc:
            self._init_error='無法開啟地圖：'+str(exc)
            print(traceback.format_exc())
        while not self._closed:
            try:packet=self._queue.get(timeout=.25)
            except queue.Empty:continue
            if packet is None:break
            ident=packet['id'];op=str(packet.get('op',''))
            if ident in self._replies:
                self._send(self._replies[ident]);continue
            try:
                if self._session is None:raise RuntimeError(self._init_error)
                if op in ('close','open_asset_editor'):
                    self._next_action='assets' if op=='open_asset_editor' else None
                    result={'closing':True}
                else:result=self._session.handle(op,packet.get('data') or {})
                reply={'id':ident,'ok':True,'data':result}
            except Exception as exc:
                print('FIX94',op,traceback.format_exc())
                reply={'id':ident,'ok':False,'error':str(exc)[:500]}
            self._replies[ident]=reply
            # Bounded replay cache: enough for delayed acknowledgements, no
            # retained full map snapshot / unbounded base64 thumbnail history.
            self._replies_bytes += len(json.dumps(reply,ensure_ascii=True))
            while len(self._replies)>24 or (self._replies_bytes>2*1024*1024 and len(self._replies)>1):
                _,old=self._replies.popitem(last=False)
                self._replies_bytes -= len(json.dumps(old,ensure_ascii=True))
            self._send(reply)
            if op in ('close','open_asset_editor') and reply['ok']:
                self._close_native();break
        self._session=None;self._replies.clear()

    def _root_owner_view_controller(self):
        try:responder=self.root.__py_view__.managed
        except Exception:return None
        for _ in range(16):
            try:responder=responder.nextResponder
            except Exception:responder=None
            if responder is None:return None
            try:
                getattr(responder,"presentingViewController");getattr(responder,"dismissViewControllerAnimated");getattr(responder,"view")
                return responder
            except Exception:pass
        return None

    def _top_presented_view_controller(self):
        try:
            native=self.root.__py_view__.managed;window=native.window
            if window is None:return None
            controller=window.rootViewController
            for _ in range(16):
                if controller is None:break
                try:presented=controller.presentedViewController
                except Exception:presented=None
                if presented is None:break
                controller=presented
            return controller
        except Exception:return None

    @mainthread
    def _close_native(self):
        # FIX94: PyView.close() can become a no-op for a fullscreen WKWebView
        # presentation in Pyto. Dismiss the exact presenting controller, the
        # same proven strategy used by the asset editor and main game.
        self._closed=True
        try:self.web.did_receive_message=None
        except Exception:pass
        closed=False
        try:
            owner=self._root_owner_view_controller()
            if owner is not None:
                try:presenter=owner.presentingViewController
                except Exception:presenter=None
                if presenter is not None:presenter.dismissViewControllerAnimated(False,completion=None)
                else:owner.dismissViewControllerAnimated(False,completion=None)
                closed=True
        except Exception as exc:print('FIX94 close owner:',repr(exc))
        if not closed:
            try:
                controller=self._top_presented_view_controller(); presenter=getattr(controller,'presentingViewController',None) if controller is not None else None
                if presenter is not None:presenter.dismissViewControllerAnimated(False,completion=None);closed=True
            except Exception as exc:print('FIX94 close fallback:',repr(exc))
        if not closed:
            try:self.root.close()
            except Exception:pass
        self._close_completed.set()

    def close(self):self._close_native()

    def run(self):
        # show_view blocks until dismissed. Keep transitions on the script's
        # normal presentation path; never open another editor in a JS callback.
        while True:
            ui.show_view(self.root,PRESENT_FULLSCREEN)
            next_action=self._next_action
            self._closed=True
            try:self._queue.put_nowait(None)
            except queue.Full:pass
            if self._worker and self._worker is not threading.current_thread():self._worker.join(timeout=1.0)
            if next_action!='assets':break
            from asset_editor.ui import AssetEditorUI
            AssetEditorUI(self.project_root).present()
            self.__init__(self.project_root)
