# run with "sudo mn --custom sdn-labs/ring.py --topo Ring --controller remote"

from mininet.topo import Topo

class Ring( Topo ):
    def __init__( self ):

        Topo.__init__( self )

        # Add switches
        switches = []
        for i in range(1, 6):
            switch = 's{}'.format(i)
            self.addSwitch(switch)
            switches.append(switch)

        for i in range(5):
            self.addLink(switches[i], switches[(i + 1) % 5])

        server = self.addHost('server')
        client = self.addHost('client')

        # Connect server and client to switches
        self.addLink(server, switches[0])
        self.addLink(client, switches[2])
 
topos = { 'Ring': ( lambda: Ring() ) }