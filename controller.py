# Software Defined Network (SDN) Controller
# Measures the delays of the links between switches.
#
# Author: Simone Francesco Curci, Marco Febbo, Amina El Kharouai, Pietro Ghersetich
#
# Usage:
# Run with topology discovery enabled:
# ryu-manager --observe-links path/to/file
# If performance issues arise, restart the VM.

import struct
import time
import networkx as nx
from ryu.base import app_manager
from ryu.controller import ofp_event, dpset
from ryu.controller.handler import set_ev_cls, CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.ofproto import ofproto_v1_3
from ryu.topology.api import get_all_link, get_all_host, get_all_switch
from ryu.lib.packet import packet, ethernet, ether_types, arp
from ryu.lib import hub

class SDNController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(SDNController, self).__init__(*args, **kwargs)
        self.enable_debug = False
        self.iterations = 1000
        self.enable_prettier_id = False
        self.switches = []
        self.links = []
        self.thread_ping = None
        self.thread_delay = None
        self.temp_delays = {}
        self.link_delays = {}
        self.echo_pkt_timestamps = {}
        self.echo_pkt_delays = {}
        self.event_delay = hub.Event()
        self.event_echo = hub.Event()
        self.switch_prettier_id = {}
        self.prettier_id_cont = 1

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def switch_event(self, ev):
        self.topology_update_handler()

    @set_ev_cls(ofp_event.EventOFPPortStateChange)
    def link_event(self, ev):
        self.topology_update_handler()

    def topology_update_handler(self):
        print("Network topology changed, recalculating delays...")
        if self.thread_ping:
            self.thread_ping.kill()
        if self.thread_delay:
            self.thread_delay.kill()
        self.switches = get_all_switch(self)
        self.links = get_all_link(self)
        self.thread_ping = hub.spawn(self.ping)
        self.thread_delay = hub.spawn(self.init_delay_measurement)

    def init_delay_measurement(self):
        hub.sleep(10)
        self.echo_pkt_timestamps.clear()
        self.echo_pkt_delays.clear()
        self.temp_delays.clear()
        self.link_delays.clear()
        self.event_echo.clear()
        self.event_delay.clear()
        if len(self.switches) < 2:
            print("Insufficient switches, waiting for updates...")
            return
        self.calculate_switch_controller_delays()
        self.delay_measurement()

    def calculate_switch_controller_delays(self):
        for _ in range(self.iterations):
            self.event_echo.wait()
            self.event_echo.clear()
        for switch in self.switches:
            self.echo_pkt_delays[switch.dp.id] /= self.iterations

    def delay_measurement(self):
        while True:
            self.measurement_pkts_cont = 0
            for switch in self.switches:
                self.send_measurement_pkt(switch)
            self.event_delay.wait(5)
            self.event_delay.clear()
            if self.measurement_pkts_cont != self.max_measurement_pkts:
                print("Packet loss detected during delay measurement.")
            else:
                self.print_delay_link()
                hub.sleep(10)

    def send_measurement_pkt(self, switch):
        datapath = switch.dp
        parser = datapath.ofproto_parser
        self.temp_delays[datapath.id] = {}
        self.link_delays[datapath.id] = {}
        for port in switch.ports:
            pkt = packet.Packet()
            eth = ethernet.ethernet(ethertype=0x902B, src=port.hw_addr, dst='ff:ff:ff:ff:ff:ff')
            payload = struct.pack("!QI", datapath.id, port.port_no)
            pkt.add_protocol(eth)
            pkt.add_protocol(payload)
            pkt.serialize()
            actions = [parser.OFPActionOutput(port.port_no)]
            out = parser.OFPPacketOut(datapath=datapath, buffer_id=0, in_port=0, actions=actions, data=pkt.data)
            self.temp_delays[datapath.id][port.port_no] = time.time()
            datapath.send_msg(out)
            if self.enable_debug:
                print(f"Sent measurement packet to switch {switch.dp.id} on port {port.port_no}")

    def print_delay_link(self):
        print("Link delays:")
        for switch1 in self.switches:
            for switch2_id in self.link_delays[switch1.dp.id]:
                delay_controller = self.echo_pkt_delays[switch1.dp.id] + self.echo_pkt_delays[switch2_id]
                delay = (self.link_delays[switch1.dp.id][switch2_id] - delay_controller) * 1000
                print(f"s{switch1.dp.id} <--> s{switch2_id}: {delay:.5f} ms")

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(datapath.ofproto.OFPIT_APPLY_ACTIONS,
                [parser.OFPActionOutput(datapath.ofproto.OFPP_CONTROLLER)])]
        mod = parser.OFPFlowMod(datapath=datapath, priority=0, match=parser.OFPMatch(), instructions=inst)
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        parser = datapath.ofproto_parser
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth.ethertype == 0x902B:
            return  # Ignore custom packets
        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            self.proxy_arp(msg)
        else:
            self.forward_packet(msg, datapath, parser, eth)

    def forward_packet(self, msg, datapath, parser, eth):
        output_port = self.find_next_hop(datapath.id, eth.dst)
        actions = [parser.OFPActionOutput(output_port)]
        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id, in_port=msg.match['in_port'], actions=actions, data=msg.data)
        datapath.send_msg(out)

# End of SDNController class
