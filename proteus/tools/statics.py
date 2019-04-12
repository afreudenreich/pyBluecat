#!/usr/bin/python
import argparse
import json
import logging
from ipaddress import ip_address, ip_network
from proteus import SOAPClient
from sys import exit


parser = argparse.ArgumentParser()
parser.add_argument('hostname', help='hostname')
parser.add_argument('-c', '--creds', help='path to file containing credentials')
group_ME = parser.add_mutually_exclusive_group(required=True)
group_ME.add_argument('-e', '--environment', help='environment from which to choose a network')
group_ME.add_argument('-i', '--ipaddr', help='Specific IP Address to reserve')
group_ME.add_argument('-n', '--network', help='network address within desired subnet')
parser.add_argument('-l', '--loglevel', choices=['warning', 'info', 'debug'], help='enable debugging')
args = parser.parse_args()

NETWORK_ENVS = {
    'lab': [ip_network(u'10.168.131.0/24'), ip_network(u'10.168.161.0/24')],
    'dev': [ip_network(u'10.57.128.0/23')],
    'test': [ip_network(u'10.57.144.0/23')],
    'stage': [ip_network(u'10.57.160.0/23')],
    'prod-ctis': [ip_network(u'10.7.96.0/23'), ip_network(u'10.7.98.0/23')],
    'prod-brad': [ip_network(u'10.107.96.0/23'), ip_network(u'10.107.98.0/23')],
    'prod-both': [ip_network(u'10.200.112.0/24'), ip_network(u'10.200.113.0/24')],
    'fmt': [ip_network(u'10.57.136.0/30'), ip_network(u'10.57.136.4/30')]
    }

if args.loglevel:
    level = getattr(logging, args.loglevel.upper())
    logging.basicConfig(level=level)

hostname = args.hostname.lower()
with open(args.creds) as f:
    creds = json.load(f)

c = SOAPClient(creds['username'], creds['password'])

# Get Networks List
if args.network:
    netAddr = args.network.split('/')[0]
    netObj = c.getIP4Network(netAddr)
    networks = [ip_network(netObj['properties']['CIDR'])]
elif args.environment:
    try:
        networks = NETWORK_ENVS[args.environment.lower()]
    except:
        print('Environment Not Found. No Network Available.')
        exit(1)
logging.info('Networks: {}'.format(str(networks)))

foundIP = False
# Check for existing IP reservations in target networks
results = c.searchByObjectTypes(hostname, 'IP4Address', 0, 1000)
logging.info(str(results))
for result in results:
    ipObj = c.apiEntityToDict(result)
    if any(ip_address(ipObj['properties']['address']) in net for net in networks):
        foundIP = True
        logging.info('Found IP already in existence: {}'.format(json.dumps(ipObj, indent=2)))
for network in networks:
    logging.info('Working through network: {}'.format(str(network)))
    # Get Network Object and set dhcp_offset based on CIDR
    netObj = c.getIP4Network(str(network.network_address))
    if network.prefixlen > 24:
        dhcp_offset = 0
    else:
        dhcp_offset = 30
    logging.info('DHCP-Offsest: {}'.format(str(dhcp_offset)))
    # Ensure IPs in the offset are 'reserved'
    while not foundIP:
        logging.info('Checking Status of Offset Addreses')
        ip = c.getNextIP4Address(netObj, offset=0)
        if ip is None:
            break
        elif network.network_address + dhcp_offset >= ip_address(unicode(ip)):
            c.assignIP4Address('', ip, '', 'MAKE_RESERVED', '')
            logging.info('Setting IP Address as RESERVED: {}'.format(ip))
        else:
            break
    # If an existing IP has not been found yet, start working through
    # every free IP in the Proteus Network until one is assigned or net is
    # exhausted
    if not foundIP:
        logging.info('Determining next available IP Address')
        while True:
            ipObj = c.assignNextAvailableIP4Address(hostname, netObj['id'])
            # None as a result indicates network has no next IP, end loop
            if ipObj.id == 0:
                break
            properties = c.propertiesStringToDict(ipObj.properties)
            logging.info('IP Address free in Proteus: {}'.format(properties['address']))
            # Check if IP has existing PTR record, if True, write it to Proteus, try next IP
            ptr = c.dns_PTR_exists(properties['address'])
            if ptr:
                logging.info('PTR Record found for Address: {}'.format(ptr))
                ipObj.name = ptr.split('.')[0]
                c.updateEntity(ipObj)
            # Try to Ping the IP address, if response, log in Proteus, try next IP
            elif c.ping(properties['address']):
                logging.info('Address responded to ping')
                ipObj.name = 'IN-USE: something pinged'
                c.updateEntity(ipObj)
            # Finally, reserve the IP in Proteus for the hostname
            else:
                logging.info('Address doesn\'t ping or have PTR record')
                foundIP = True
                ipObj = c.apiEntityToDict(ipObj)
                break
    # If an IP has been found, either new or existing, return results and exit
    if foundIP:
        network = ip_network(unicode(netObj['properties']['CIDR']))
        output = {
            'ip_addr': ipObj['properties']['address'],
            'gateway': str(network.network_address + 1),
            'net_mask': str(network.netmask),
            'net_name': netObj['name'],
            '_ipobj': ipObj,
            '_netobj': netObj
        }
        print(json.dumps(output, sort_keys=True, indent=4))
        c.logout()
        exit()
if not foundIP:
    print('No Addresses Available.')
    exit(1)
