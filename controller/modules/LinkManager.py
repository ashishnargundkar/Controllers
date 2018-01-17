# ipop-project
# Copyright 2016, University of Florida
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

from controller.framework.ControllerModule import ControllerModule
import time
import json
import threading
import uuid

class LinkManager(ControllerModule):

    def __init__(self, cfx_handle, module_config, module_name):
        super(LinkManager, self).__init__(cfx_handle, module_config, module_name)
        self.maxretries = self._cm_config["MaxConnRetry"]
        self._links = {}
        self._links_lck = threading.Lock()
        self._peers = {}
        self._overlays = {}

    def initialize(self):
        self.register_cbt('Logger', 'LOG_INFO', "Module Loaded")

    '''
    The caller provides the overlay id which contains the link and the peer id
    which the link connects. The link id is generated here and returned to the
    caller. This is done only after the local enpoint is created, but can
    occur before the link is ready. The link status can be queried to determine
    when it is writeable.
    We request creatation of the remote endpoint first to avoid cleaning up a
    local endpoint if the peer denies our request. The link id is communicated
    in the request and will be the same at both nodes.
    '''
    def req_link_endpt_from_peer(self, cbt):
        olid = cbt.Request.Params["OverlayId"]
        peerid = cbt.Request.Params["PeerId"]

        if self._overlays.get(olid) is None:
            self._overlays[olid] = dict(Lock=threading.Lock(), Peers=dict(), Links=dict())

        if peerid in self._overlays[olid]["Peers"]:
            lnkid = self._overlays[olid]["Peers"][peerid] #index for quick peer->link lookup
        else:
            lnkid = uuid.uuid4()
            self._overlays[olid]["Peers"][peerid] = lnkid
            self._overlays[olid]["Peers"][peerid]["Links"][lnkid] = dict(PeerId=peerid, Stats=dict())

        if self._links.get(olid) is None:
            self._links[olid] = {}
        if peerid in self._peers[olid]:
            lnkid = self._peers[olid][peerid]
        else:
            lnkid = uuid.uuid4()
            self._peers[olid][peerid] = lnkid
            self._links[olid][lnkid] = dict(PeerId=peerid)

        msg = {
            "OverlayId" : olid,
            "LinkId" : lnkid,
            "EncryptionEnabled" : cbt.Request.Params["EncryptionEnabled"],
            "NodeData": cbt.Request.Params["NodeData"],
            "TTL": time.time() + self._cm_config["InitialLinkTTL"]
        }

        # Send the message via SIG server to peer node
        cbt_descr = dict(OverlayId = olid,
                       RecipientCM = "LinkManager",
                       Action = "LNK_REQ_LINK_ENDPT",
                       Params = json.dumps(msg))

        payload = {"PeerId": peerid, "CbtData": cbt_descr}
        self.register_cbt("Signal", "SIG_FORWARD_CBT", payload)
        return lnkid

    def CreateLinkLocalEndpt(self, cbt):
        lcbt = self.create_linked_cbt(cbt)
        lcbt.SetRequest("TincanInterface", "TCI_CREATE_LINK", cbt.Request.Params)
        self.submit_cbt(lcbt)

    def SendLocalLinkEndptToPeer(self, cbt):
        '''
        Completes the CBT to Signal which will send it to the remote peer
        '''
        local_cas = cbt.Response.Data
        parent_cbt = self.get_parent_cbt(cbt)
        parent_cbt.SetResponse(local_cas, True)
        self.complete_cbt(parent_cbt)

        payload = {"PeerId": peerid, "CbtData": cbtdata}
        self.register_cbt("Signal", "SIG_FORWARD_CBT", payload)

    def RemoveLink(self, cbt):
        msg = cbt.Request.Params
        #send courtesy terminate link ICC

    def QueryLinkDescriptor(self, cbt):
        pass

    def process_cbt(self, cbt):
        try:
            if cbt.OpType == "Request":
                if cbt.Request.Action == "LNK_CREATE_LINK":
                    self.req_link_endpt_from_peer(cbt) #1 send via SIG

                elif cbt.Request.Action == "LNK_REQ_LINK_ENDPT":
                    self.CreateLinkLocalEndpt(cbt) #2 rcvd peer req for endpt, send via TCI 

                elif cbt.Request.Action == "LNK_ADD_PEER_CAS":
                    self.CreateLinkLocalEndpt(cbt) #4 rcvd cas from peer, sends via TCI to add peer cas

                elif cbt.Request.Action == "LNK_REMOVE_LINK":
                    self.RemoveLink(cbt)

                if cbt.Request.Action == "LNK_QUERY_LINK_DSCR":
                    pass

                if cbt.Request.Action == "SIG_PEER_PRESENCE_NOTIFY":
                    pass
                else:
                    log = "Unsupported CBT action {0}".format(cbt)
                    self.register_cbt('Logger', 'LOG_WARNING', log)

            if cbt.OpType == "Response":
                if (cbt.Response.Status == False):
                    self.register_cbt("Logger", "LOG_WARNING", "CBT failed {0}".format(cbt.Response.Message))
                    return
                if cbt.Request.Action == "SIG_FORWARD_CBT":
                    self.free_cbt(cbt)

                if cbt.Request.Action == "LNK_REQ_LINK_ENDPT":
                    pass

                if cbt.Request.Action == "TCI_CREATE_LINK":
                    self.SendLocalLinkEndptToPeer(cbt) #3/5 send via SIG to peer to update CAS
                    self.SendResponseToInitiator(cbt)

                self.free_cbt(cbt)
                
        except Exception as err:
            erlog = "Exception trace, continuing ...:\n{0}".format(traceback.format_exc())
            self.register_cbt('Logger', 'LOG_WARNING', erlog)

    def timer_method(self):
        try:
            for olid in self._overlays:
                self._overlays[olid]["Lock"].acquire()
                for linkid in self._overlays[olid]["Links"]:
                    params = {
                        "OverlayId": olid,
                        "LinkId": linkid
                        }
                    self.register_cbt("TincanInterface", "TCI_QUERY_LINK_STATS", params)
                self._overlays[olid]["Lock"].release()
        except Exception as err:
            self._overlays[olid]["Lock"].release()
            self.register_cbt('Logger', 'error', "Exception caught in LinkManager timer thread.\
                             Error: {0}".format(str(err)))

    def terminate(self):
        pass
