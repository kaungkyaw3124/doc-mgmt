# jan/02/1970 04:12:28 by RouterOS 6.49.20
# software id = IS4Y-W1Y6
#
# model = RB2011UiAS
# serial number = HDJ08G6KHDZ
/interface bridge
add name=bridge-LAN vlan-filtering=yes
/interface ethernet
set [ find default-name=ether4 ] name=CCTV-port
set [ find default-name=ether3 ] name=LAN-trunk
set [ find default-name=ether5 ] comment="reserved - not in use" disabled=yes \
    name=Reserved-5
set [ find default-name=ether6 ] comment="reserved - not in use" disabled=yes \
    name=Reserved-6
set [ find default-name=ether7 ] comment="reserved - not in use" disabled=yes \
    name=Reserved-7
set [ find default-name=ether8 ] comment="reserved - not in use" disabled=yes \
    name=Reserved-8
set [ find default-name=ether1 ] name=SRV-trunk-A
set [ find default-name=ether2 ] name=SRV-trunk-B
set [ find default-name=ether9 ] name=WAN1
set [ find default-name=ether10 ] name=WAN2
/interface vlan
add interface=bridge-LAN name=vlan1-LAN vlan-id=1
add interface=bridge-LAN name=vlan20-WIFI vlan-id=20
add interface=bridge-LAN name=vlan30-CCTV vlan-id=30
add interface=bridge-LAN name=vlan40-SRVLOC vlan-id=40
add interface=bridge-LAN name=vlan50-SRVPUB vlan-id=50
/interface list
add name=WAN
add name=LAN
/interface wireless security-profiles
set [ find default=yes ] supplicant-identity=MikroTik
/ip pool
add name=pool-WIFI ranges=192.168.100.10-192.168.100.199
add name=pool-CCTV ranges=172.20.30.10-172.20.30.254
add name=dhcp_pool3 ranges=172.20.10.2-172.20.10.20
/ip dhcp-server
add address-pool=pool-WIFI disabled=no interface=vlan20-WIFI lease-time=1d \
    name=dhcp-WIFI
add address-pool=pool-CCTV disabled=no interface=vlan30-CCTV lease-time=1d \
    name=dhcp-CCTV
add address-pool=dhcp_pool3 disabled=no interface=vlan1-LAN name=dhcp1
/interface bridge port
add bridge=bridge-LAN comment="Server Farm trunk A" frame-types=\
    admit-only-vlan-tagged interface=SRV-trunk-A
add bridge=bridge-LAN comment="Server Farm trunk B" frame-types=\
    admit-only-vlan-tagged interface=SRV-trunk-B
add bridge=bridge-LAN comment="LAN native=VLAN1, WiFi AP tagged=VLAN20" \
    interface=LAN-trunk
add bridge=bridge-LAN comment="CCTV access port" interface=CCTV-port pvid=30
/interface bridge vlan
add bridge=bridge-LAN tagged=bridge-LAN untagged=LAN-trunk vlan-ids=1
add bridge=bridge-LAN tagged=bridge-LAN,LAN-trunk vlan-ids=20
add bridge=bridge-LAN tagged=bridge-LAN untagged=CCTV-port vlan-ids=30
add bridge=bridge-LAN tagged=bridge-LAN,SRV-trunk-A,SRV-trunk-B vlan-ids=40
add bridge=bridge-LAN tagged=bridge-LAN,SRV-trunk-A,SRV-trunk-B vlan-ids=50
/interface list member
add interface=WAN1 list=WAN
add interface=WAN2 list=WAN
add interface=vlan1-LAN list=LAN
add interface=vlan20-WIFI list=LAN
add interface=vlan30-CCTV list=LAN
add interface=vlan40-SRVLOC list=LAN
add interface=vlan50-SRVPUB list=LAN
/ip address
add address=172.20.10.1/24 comment=LAN interface=vlan1-LAN network=\
    172.20.10.0
add address=192.168.100.1/24 comment="WiFi + Printers" interface=vlan20-WIFI \
    network=192.168.100.0
add address=172.20.30.1/24 comment=CCTV interface=vlan30-CCTV network=\
    172.20.30.0
add address=172.20.40.1/24 comment="Local Servers" interface=vlan40-SRVLOC \
    network=172.20.40.0
add address=172.20.50.1/24 comment="Public Servers" interface=vlan50-SRVPUB \
    network=172.20.50.0
/ip dhcp-client
add add-default-route=no comment=WAN1 disabled=no interface=WAN1 script="if (\
    \$bound=1) do={if ([/ip route find comment=\"WAN1-default\"] = \"\") do={/\
    ip route add dst-address=0.0.0.0/0 gateway=\$\"gateway-address\" routing-m\
    ark=to-WAN1 distance=1 check-gateway=ping comment=\"WAN1-default\"} else={\
    /ip route set [find comment=\"WAN1-default\"] gateway=\$\"gateway-address\
    \"}} else={/ip route remove [find comment=\"WAN1-default\"]}}" \
    use-peer-dns=no
add add-default-route=no comment=WAN2 disabled=no interface=WAN2 script="if (\
    \$bound=1) do={if ([/ip route find comment=\"WAN2-default\"] = \"\") do={/\
    ip route add dst-address=0.0.0.0/0 gateway=\$\"gateway-address\" routing-m\
    ark=to-WAN2 distance=1 check-gateway=ping comment=\"WAN2-default\"} else={\
    /ip route set [find comment=\"WAN2-default\"] gateway=\$\"gateway-address\
    \"}} else={/ip route remove [find comment=\"WAN2-default\"]}}" \
    use-peer-dns=no
/ip dhcp-server network
add address=172.20.1.0/24 dns-server=172.20.1.1 gateway=172.20.1.1
add address=172.20.10.0/24 dns-server=172.20.10.1 gateway=172.20.10.1 \
    netmask=24
add address=172.20.30.0/24 dns-server=172.20.30.1 gateway=172.20.30.1
add address=192.168.100.0/24 dns-server=192.168.100.1 gateway=192.168.100.1
/ip firewall address-list
add address=192.168.100.200-192.168.100.250 comment=\
    "reserved printer range - not a real rule, just documents the block" \
    list=printers
/ip firewall filter
add action=accept chain=input comment="allow established/related" \
    connection-state=established,related
add action=drop chain=input comment="drop invalid" connection-state=invalid
add action=accept chain=input comment="allow LAN/SRV -> router mgmt" \
    in-interface-list=LAN
add action=accept chain=input comment="allow ping from WAN (optional)" \
    in-interface-list=WAN protocol=icmp
add action=drop chain=input comment="drop everything else WAN -> router" \
    in-interface-list=WAN
add action=accept chain=forward connection-state=established,related
add action=drop chain=forward connection-state=invalid
add action=accept chain=forward comment="LAN/SRV -> Internet" \
    in-interface-list=LAN out-interface-list=WAN
add action=drop chain=forward comment=\
    "block unsolicited inbound (dst-nat rules above are exempt)" \
    connection-state=new in-interface-list=WAN out-interface-list=LAN
/ip firewall mangle
add action=mark-connection chain=prerouting comment="PCC WAN1 - LAN" \
    connection-state=new dst-address-type=!local in-interface-list=LAN \
    new-connection-mark=WAN1-conn passthrough=yes per-connection-classifier=\
    both-addresses-and-ports:2/0
add action=mark-connection chain=prerouting comment="PCC WAN2 - LAN" \
    connection-state=new dst-address-type=!local in-interface-list=LAN \
    new-connection-mark=WAN2-conn passthrough=yes per-connection-classifier=\
    both-addresses-and-ports:2/1
add action=mark-connection chain=output comment="PCC WAN1 - router" \
    connection-state=new dst-address-type=!local new-connection-mark=\
    WAN1-conn passthrough=yes per-connection-classifier=\
    both-addresses-and-ports:2/0
add action=mark-connection chain=output comment="PCC WAN2 - router" \
    connection-state=new dst-address-type=!local new-connection-mark=\
    WAN2-conn passthrough=yes per-connection-classifier=\
    both-addresses-and-ports:2/1
add action=mark-routing chain=prerouting comment="Route LAN to WAN1" \
    connection-mark=WAN1-conn in-interface-list=LAN new-routing-mark=to-WAN1 \
    passthrough=yes
add action=mark-routing chain=prerouting comment="Route LAN to WAN2" \
    connection-mark=WAN2-conn in-interface-list=LAN new-routing-mark=to-WAN2 \
    passthrough=yes
add action=mark-routing chain=output connection-mark=WAN1-conn \
    new-routing-mark=to-WAN1 passthrough=yes
add action=mark-routing chain=output connection-mark=WAN2-conn \
    new-routing-mark=to-WAN2 passthrough=yes
/ip firewall nat
add action=masquerade chain=srcnat comment="NAT both WANs" \
    out-interface-list=WAN
/ip route
add check-gateway=ping comment=WAN1-default distance=1 gateway=172.20.0.1 \
    routing-mark=to-WAN1
add check-gateway=ping comment=WAN2-default distance=1 gateway=172.20.1.1 \
    routing-mark=to-WAN2
