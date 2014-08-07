# Copyright 2012, Contrail Systems, Inc.
#

"""
.. attention:: Fix the license string
"""
import requests
import re
import uuid
import json
import time
import socket
import netaddr
from netaddr import IPNetwork, IPSet, IPAddress
import gevent
import bottle

from neutron.common import constants
from neutron.common import exceptions
from neutron.api.v2 import attributes as attr

from cfgm_common import exceptions as vnc_exc
from vnc_api.vnc_api import *

_DEFAULT_HEADERS = {
    'Content-type': 'application/json; charset="UTF-8"', }

# TODO find if there is a common definition
CREATE = 1
READ = 2
UPDATE = 3
DELETE = 4

IP_PROTOCOL_MAP = {constants.PROTO_NAME_TCP: constants.PROTO_NUM_TCP,
                   constants.PROTO_NAME_UDP: constants.PROTO_NUM_UDP,
                   constants.PROTO_NAME_ICMP: constants.PROTO_NUM_ICMP,
                   constants.PROTO_NAME_ICMP_V6: constants.PROTO_NUM_ICMP_V6}

# SNAT defines
SNAT_SERVICE_TEMPLATE_FQ_NAME = ['default-domain', 'netns-snat-template']

# Security group Exceptions
class SecurityGroupInvalidPortRange(exceptions.InvalidInput):
    message = _("For TCP/UDP protocols, port_range_min must be "
                "<= port_range_max")


class SecurityGroupInvalidPortValue(exceptions.InvalidInput):
    message = _("Invalid value for port %(port)s")


class SecurityGroupInvalidIcmpValue(exceptions.InvalidInput):
    message = _("Invalid value for ICMP %(field)s (%(attr)s) "
                "%(value)s. It must be 0 to 255.")


class SecurityGroupMissingIcmpType(exceptions.InvalidInput):
    message = _("ICMP code (port-range-max) %(value)s is provided"
                " but ICMP type (port-range-min) is missing.")


class SecurityGroupInUse(exceptions.InUse):
    message = _("Security Group %(id)s in use.")


class SecurityGroupCannotRemoveDefault(exceptions.InUse):
    message = _("Removing default security group not allowed.")


class SecurityGroupCannotUpdateDefault(exceptions.InUse):
    message = _("Updating default security group not allowed.")


class SecurityGroupDefaultAlreadyExists(exceptions.InUse):
    message = _("Default security group already exists.")


class SecurityGroupRuleInvalidProtocol(exceptions.InvalidInput):
    message = _("Security group rule protocol %(protocol)s not supported. "
                "Only protocol values %(values)s and their integer "
                "representation (0 to 255) are supported.")

class SecurityGroupRulesNotSingleTenant(exceptions.InvalidInput):
    message = _("Multiple tenant_ids in bulk security group rule create"
                " not allowed")


class SecurityGroupRemoteGroupAndRemoteIpPrefix(exceptions.InvalidInput):
    message = _("Only remote_ip_prefix or remote_group_id may "
                "be provided.")


class SecurityGroupProtocolRequiredWithPorts(exceptions.InvalidInput):
    message = _("Must also specify protocol if port range is given.")


class SecurityGroupNotSingleGroupRules(exceptions.InvalidInput):
    message = _("Only allowed to update rules for "
                "one security profile at a time")


class SecurityGroupNotFound(exceptions.NotFound):
    message = _("Security group %(id)s does not exist")


class SecurityGroupRuleNotFound(exceptions.NotFound):
    message = _("Security group rule %(id)s does not exist")


class DuplicateSecurityGroupRuleInPost(exceptions.InUse):
    message = _("Duplicate Security Group Rule in POST.")


class SecurityGroupRuleExists(exceptions.InUse):
    message = _("Security group rule already exists. Group id is %(id)s.")


class SecurityGroupRuleParameterConflict(exceptions.InvalidInput):
    message = _("Conflicting value ethertype %(ethertype)s for CIDR %(cidr)s")


# L3 Exceptions
class RouterNotFound(exceptions.NotFound):
    message = _("Router %(router_id)s could not be found")


class RouterInUse(exceptions.InUse):
    message = _("Router %(router_id)s still has ports")


class RouterInterfaceNotFound(exceptions.NotFound):
    message = _("Router %(router_id)s does not have "
                "an interface with id %(port_id)s")


class RouterInterfaceNotFoundForSubnet(exceptions.NotFound):
    message = _("Router %(router_id)s has no interface "
                "on subnet %(subnet_id)s")


class RouterInterfaceInUseByFloatingIP(exceptions.InUse):
    message = _("Router interface for subnet %(subnet_id)s on router "
                "%(router_id)s cannot be deleted, as it is required "
                "by one or more floating IPs.")


class FloatingIPNotFound(exceptions.NotFound):
    message = _("Floating IP %(floatingip_id)s could not be found")


class ExternalGatewayForFloatingIPNotFound(exceptions.NotFound):
    message = _("External network %(external_network_id)s is not reachable "
                "from subnet %(subnet_id)s.  Therefore, cannot associate "
                "Port %(port_id)s with a Floating IP.")


class FloatingIPPortAlreadyAssociated(exceptions.InUse):
    message = _("Cannot associate floating IP %(floating_ip_address)s "
                "(%(fip_id)s) with port %(port_id)s "
                "using fixed IP %(fixed_ip)s, as that fixed IP already "
                "has a floating IP on external network %(net_id)s.")


class L3PortInUse(exceptions.InUse):
    message = _("Port %(port_id)s has owner %(device_owner)s and therefore"
                " cannot be deleted directly via the port API.")


class RouterExternalGatewayInUseByFloatingIp(exceptions.InUse):
    message = _("Gateway cannot be updated for router %(router_id)s, since a "
                "gateway to external network %(net_id)s is required by one or "
                "more floating IPs.")

# Allowed Address Pair
class AddressPairMatchesPortFixedIPAndMac(exceptions.InvalidInput):
    message = _("Port's Fixed IP and Mac Address match an address pair entry.")

class DBInterface(object):
    """
    An instance of this class forwards requests to vnc cfg api (web)server
    """
    Q_URL_PREFIX = '/extensions/ct'

    def __init__(self, admin_name, admin_password, admin_tenant_name,
                 api_srvr_ip, api_srvr_port, user_info=None,
                 contrail_extensions_enabled=True,
                 list_optimization_enabled=False):
        self._api_srvr_ip = api_srvr_ip
        self._api_srvr_port = api_srvr_port

        self._db_cache = {}
        self._db_cache['q_networks'] = {}
        self._db_cache['q_subnets'] = {}
        self._db_cache['q_subnet_maps'] = {}
        self._db_cache['q_policies'] = {}
        self._db_cache['q_ipams'] = {}
        self._db_cache['q_routers'] = {}
        self._db_cache['q_floatingips'] = {}
        self._db_cache['q_ports'] = {}
        self._db_cache['q_fixed_ip_to_subnet'] = {}
        #obj-uuid to tenant-uuid mapping
        self._db_cache['q_obj_to_tenant'] = {}
        self._db_cache['q_tenant_to_def_sg'] = {}
        #port count per tenant-id
        self._db_cache['q_tenant_port_count'] = {}
        self._db_cache['vnc_networks'] = {}
        self._db_cache['vnc_ports'] = {}
        self._db_cache['vnc_projects'] = {}
        self._db_cache['vnc_instance_ips'] = {}
        self._db_cache['vnc_routers'] = {}

        self._contrail_extensions_enabled = contrail_extensions_enabled
        self._list_optimization_enabled = list_optimization_enabled

        # Retry till a api-server is up
        connected = False
        while not connected:
            try:
                # TODO remove hardcode
                self._vnc_lib = VncApi(admin_name, admin_password,
                                       admin_tenant_name, api_srvr_ip,
                                       api_srvr_port, '/', user_info=user_info)
                connected = True
            except requests.exceptions.RequestException as e:
                gevent.sleep(3)

        # TODO remove this backward compat code eventually
        # changes 'net_fq_name_str pfx/len' key to 'net_id pfx/len' key
        subnet_map = self._vnc_lib.kv_retrieve(key=None)
        for kv_dict in subnet_map:
            key = kv_dict['key']
            if len(key.split()) == 1:
                subnet_id = key
                # uuid key, fixup value portion to 'net_id pfx/len' format
                # if not already so
                if len(kv_dict['value'].split(':')) == 1:
                    # new format already, skip
                    continue

                net_fq_name = kv_dict['value'].split()[0].split(':')
                try:
                    net_obj = self._virtual_network_read(fq_name=net_fq_name)
                except NoIdError:
                    self._vnc_lib.kv_delete(subnet_id)
                    continue

                new_subnet_key = '%s %s' % (net_obj.uuid,
                                            kv_dict['value'].split()[1])
                self._vnc_lib.kv_store(subnet_id, new_subnet_key)
            else:  # subnet key
                if len(key.split()[0].split(':')) == 1:
                    # new format already, skip
                    continue

                # delete old key, convert to new key format and save
                old_subnet_key = key
                self._vnc_lib.kv_delete(old_subnet_key)

                subnet_id = kv_dict['value']
                net_fq_name = key.split()[0].split(':')
                try:
                    net_obj = self._virtual_network_read(fq_name=net_fq_name)
                except NoIdError:
                    continue

                new_subnet_key = '%s %s' % (net_obj.uuid, key.split()[1])
                self._vnc_lib.kv_store(new_subnet_key, subnet_id)
    #end __init__

    # Helper routines
    def _request_api_server(self, url, method, data=None, headers=None):
        if method == 'GET':
            return requests.get(url)
        if method == 'POST':
            return requests.post(url, data=data, headers=headers)
        if method == 'DELETE':
            return requests.delete(url)
    #end _request_api_server

    def _relay_request(self, request):
        """
        Send received request to api server
        """
        # chop neutron parts of url and add api server address
        url_path = re.sub(self.Q_URL_PREFIX, '', request.environ['PATH_INFO'])
        url = "http://%s:%s%s" % (self._api_srvr_ip, self._api_srvr_port,
                                  url_path)

        return self._request_api_server(
            url, request.environ['REQUEST_METHOD'],
            request.body, {'Content-type': request.environ['CONTENT_TYPE']})
    #end _relay_request

    def _obj_to_dict(self, obj):
        return self._vnc_lib.obj_to_dict(obj)
    #end _obj_to_dict

    def _get_plugin_property(self, property_in):
        fq_name=['default-global-system-config'];
        gsc_obj = self._vnc_lib.global_system_config_read(fq_name);
        plugin_settings = gsc_obj.plugin_tuning.plugin_property
        for each_setting in plugin_settings:
            if each_setting.property == property_in:
                return each_setting.value
        return None
    #end _get_plugin_property

    def _ensure_instance_exists(self, instance_id):
        instance_name = instance_id
        instance_obj = VirtualMachine(instance_name)
        try:
            id = self._vnc_lib.obj_to_id(instance_obj)
            instance_obj = self._vnc_lib.virtual_machine_read(id=id)
        except NoIdError:  # instance doesn't exist, create it
            # check if instance_id is a uuid value or not
            try:
                uuid.UUID(instance_id)
                instance_obj.uuid = instance_id
            except ValueError:
                # if instance_id is not a valid uuid, let
                # virtual_machine_create generate uuid for the vm
                pass
            self._vnc_lib.virtual_machine_create(instance_obj)

        return instance_obj
    #end _ensure_instance_exists

    def _ensure_default_security_group_exists(self, proj_id):
        # check in cache
        sg_uuid = self._db_cache_read('q_tenant_to_def_sg', proj_id)
        if sg_uuid:
            return

        # check in api server
        proj_obj = self._vnc_lib.project_read(id=proj_id)
        sg_groups = proj_obj.get_security_groups()
        for sg_group in sg_groups or []:
            if sg_group['to'][-1] == 'default':
                self._db_cache_write('q_tenant_to_def_sg',
                                     proj_id, sg_group['uuid'])
                return

        # does not exist hence create and add cache
        sg_uuid = str(uuid.uuid4())
        self._db_cache_write('q_tenant_to_def_sg', proj_id, sg_uuid)
        sg_obj = SecurityGroup(name='default', parent_obj=proj_obj)
        sg_obj.uuid = sg_uuid
        self._vnc_lib.security_group_create(sg_obj)

        #allow all egress traffic
        def_rule = {}
        def_rule['port_range_min'] = 0
        def_rule['port_range_max'] = 65535
        def_rule['direction'] = 'egress'
        def_rule['remote_ip_prefix'] = '0.0.0.0/0'
        def_rule['remote_group_id'] = None
        def_rule['protocol'] = 'any'
        rule = self._security_group_rule_neutron_to_vnc(def_rule, CREATE)
        self._security_group_rule_create(sg_obj.uuid, rule)

        #allow ingress traffic from within default security group
        def_rule = {}
        def_rule['port_range_min'] = 0
        def_rule['port_range_max'] = 65535
        def_rule['direction'] = 'ingress'
        def_rule['remote_ip_prefix'] = '0.0.0.0/0'
        def_rule['remote_group_id'] = None
        def_rule['protocol'] = 'any'
        rule = self._security_group_rule_neutron_to_vnc(def_rule, CREATE)
        self._security_group_rule_create(sg_obj.uuid, rule)
    #end _ensure_default_security_group_exists

    def _db_cache_read(self, table, key):
        try:
            return self._db_cache[table][key]
        except KeyError:
            return None
    #end _db_cache_read

    def _db_cache_write(self, table, key, val):
        self._db_cache[table][key] = val
    #end _db_cache_write

    def _db_cache_delete(self, table, key):
        try:
            del self._db_cache[table][key]
        except Exception:
            pass
    #end _db_cache_delete

    def _db_cache_flush(self, table):
        self._db_cache[table] = {}
    #end _db_cache_delete

    def _get_obj_tenant_id(self, q_type, obj_uuid):
        # Get the mapping from cache, else seed cache and return
        try:
            return self._db_cache['q_obj_to_tenant'][obj_uuid]
        except KeyError:
            # Seed the cache and return
            if q_type == 'port':
                port_obj = self._virtual_machine_interface_read(obj_uuid)
                if port_obj.parent_type != "project":
                    net_id = port_obj.get_virtual_network_refs()[0]['uuid']
                    # recurse up type-hierarchy
                    tenant_id = self._get_obj_tenant_id('network', net_id)
                else:
                    tenant_id = port_obj.parent_uuid.replace('-', '')
                self._set_obj_tenant_id(obj_uuid, tenant_id)
                return tenant_id

            if q_type == 'network':
                net_obj = self._virtual_network_read(net_id=obj_uuid)
                tenant_id = net_obj.parent_uuid.replace('-', '')
                self._set_obj_tenant_id(obj_uuid, tenant_id)
                return tenant_id

            return None
    #end _get_obj_tenant_id

    def _set_obj_tenant_id(self, obj_uuid, tenant_uuid):
        self._db_cache['q_obj_to_tenant'][obj_uuid] = tenant_uuid
    #end _set_obj_tenant_id

    def _del_obj_tenant_id(self, obj_uuid):
        try:
            del self._db_cache['q_obj_to_tenant'][obj_uuid]
        except Exception:
            pass
    #end _del_obj_tenant_id

    def _project_read(self, proj_id=None, fq_name=None):
        if proj_id:
            try:
                # disable cache for now as fip pool might be put without
                # neutron knowing it
                raise KeyError
                #return self._db_cache['vnc_projects'][proj_id]
            except KeyError:
                proj_obj = self._vnc_lib.project_read(id=proj_id)
                fq_name_str = json.dumps(proj_obj.get_fq_name())
                self._db_cache['vnc_projects'][proj_id] = proj_obj
                self._db_cache['vnc_projects'][fq_name_str] = proj_obj
                return proj_obj

        if fq_name:
            fq_name_str = json.dumps(fq_name)
            try:
                # disable cache for now as fip pool might be put without
                # neutron knowing it
                raise KeyError
                #return self._db_cache['vnc_projects'][fq_name_str]
            except KeyError:
                proj_obj = self._vnc_lib.project_read(fq_name=fq_name)
                self._db_cache['vnc_projects'][fq_name_str] = proj_obj
                self._db_cache['vnc_projects'][proj_obj.uuid] = proj_obj
                return proj_obj
    #end _project_read

    def _raise_contrail_exception(self, code, exc):
        exc_info = {'message': str(exc)}
        bottle.abort(code, json.dumps(exc_info))

    def _security_group_rule_create(self, sg_id, sg_rule):
        try:
            sg_vnc = self._vnc_lib.security_group_read(id=sg_id)
        except NoIdError:
            self._raise_contrail_exception(404, SecurityGroupNotFound(id=sg_id))

        rules = sg_vnc.get_security_group_entries()
        if rules is None:
            rules = PolicyEntriesType([sg_rule])
        else:
            rules.add_policy_rule(sg_rule)

        sg_vnc.set_security_group_entries(rules)
        self._vnc_lib.security_group_update(sg_vnc)
        return
    #end _security_group_rule_create

    def _security_group_rule_find(self, sgr_id):
        dom_projects = self._project_list_domain(None)
        for project in dom_projects:
            proj_id = project['uuid']
            project_sgs = self._security_group_list_project(proj_id)

            for sg_obj in project_sgs:
                sgr_entries = sg_obj.get_security_group_entries()
                if sgr_entries == None:
                    continue

                for sg_rule in sgr_entries.get_policy_rule():
                    if sg_rule.get_rule_uuid() == sgr_id:
                        return sg_obj, sg_rule

        return None, None
    #end _security_group_rule_find

    def _security_group_rule_delete(self, sg_obj, sg_rule):
        rules = sg_obj.get_security_group_entries()
        rules.get_policy_rule().remove(sg_rule)
        sg_obj.set_security_group_entries(rules)
        self._vnc_lib.security_group_update(sg_obj)
        return
    #end _security_group_rule_delete

    def _security_group_delete(self, sg_id):
        self._vnc_lib.security_group_delete(id=sg_id)
    #end _security_group_delete

    def _svc_instance_create(self, si_obj):
        si_uuid = self._vnc_lib.service_instance_create(si_obj)
        st_fq_name = ['default-domain', 'nat-template']
        st_obj = self._vnc_lib.service_template_read(fq_name=st_fq_name)
        si_obj.set_service_template(st_obj)
        self._vnc_lib.service_instance_update(si_obj)

        return si_uuid
    #end _svc_instance_create

    def _svc_instance_delete(self, si_id):
        self._vnc_lib.service_instance_delete(id=si_id)
    #end _svc_instance_delete

    def _route_table_create(self, rt_obj):
        rt_uuid = self._vnc_lib.route_table_create(rt_obj)
        return rt_uuid
    #end _route_table_create

    def _route_table_delete(self, rt_id):
        self._vnc_lib.route_table_delete(id=rt_id)
    #end _route_table_delete

    def _resource_create(self, resource_type, obj):
        try:
            obj_uuid = getattr(self._vnc_lib, resource_type + '_create')(obj)
        except RefsExistError:
            obj.uuid = str(uuid.uuid4())
            obj.name += '-' + obj.uuid
            obj.fq_name[-1] += '-' + obj.uuid
            obj_uuid = getattr(self._vnc_lib, resource_type + '_create')(obj)

        return obj_uuid
    #end _resource_create

    def _virtual_network_read(self, net_id=None, fq_name=None, fields=None):
        if net_id:
            try:
                # return self._db_cache['vnc_networks'][net_id]
                raise KeyError
            except KeyError:
                net_obj = self._vnc_lib.virtual_network_read(id=net_id,
                                                             fields=fields)
                fq_name_str = json.dumps(net_obj.get_fq_name())
                self._db_cache['vnc_networks'][net_id] = net_obj
                self._db_cache['vnc_networks'][fq_name_str] = net_obj
                return net_obj

        if fq_name:
            fq_name_str = json.dumps(fq_name)
            try:
                # return self._db_cache['vnc_networks'][fq_name_str]
                raise KeyError
            except KeyError:
                net_obj = self._vnc_lib.virtual_network_read(fq_name=fq_name,
                                                             fields=fields)
                self._db_cache['vnc_networks'][fq_name_str] = net_obj
                self._db_cache['vnc_networks'][net_obj.uuid] = net_obj
                return net_obj

    #end _virtual_network_read

    def _virtual_network_update(self, net_obj):
        self._vnc_lib.virtual_network_update(net_obj)
        # read back to get subnet gw allocated by api-server
        fq_name_str = json.dumps(net_obj.get_fq_name())

        self._db_cache['vnc_networks'][net_obj.uuid] = net_obj
        self._db_cache['vnc_networks'][fq_name_str] = net_obj
    #end _virtual_network_update

    def _virtual_network_delete(self, net_id):
        fq_name_str = None
        try:
            net_obj = self._db_cache['vnc_networks'][net_id]
            fq_name_str = json.dumps(net_obj.get_fq_name())
        except KeyError:
            net_obj = None

        try:
            if net_obj and net_obj.get_floating_ip_pools():
                fip_pools = net_obj.get_floating_ip_pools()
                for fip_pool in fip_pools:
                    self._floating_ip_pool_delete(fip_pool_id=fip_pool['uuid'])

            self._vnc_lib.virtual_network_delete(id=net_id)
        except RefsExistError:
            self._raise_contrail_exception(404, exceptions.NetworkInUse(net_id=net_id))

        try:
            del self._db_cache['vnc_networks'][net_id]
            if fq_name_str:
                del self._db_cache['vnc_networks'][fq_name_str]
        except KeyError:
            pass
    #end _virtual_network_delete

    def _virtual_network_list(self, parent_id=None, obj_uuids=None,
                              fields=None, detail=False, count=False):
        return self._vnc_lib.virtual_networks_list(
                                              parent_id=parent_id,
                                              obj_uuids=obj_uuids,
                                              fields=fields,
                                              detail=detail,
                                              count=count)
    #end _virtual_network_list

    def _virtual_machine_interface_read(self, port_id=None, fq_name=None,
                                        fields=None):
        if port_id:
            try:
                # return self._db_cache['vnc_ports'][port_id]
                raise KeyError
            except KeyError:
                port_obj = self._vnc_lib.virtual_machine_interface_read(
                    id=port_id, fields=fields)
                fq_name_str = json.dumps(port_obj.get_fq_name())
                self._db_cache['vnc_ports'][port_id] = port_obj
                self._db_cache['vnc_ports'][fq_name_str] = port_obj
                return port_obj

        if fq_name:
            fq_name_str = json.dumps(fq_name)
            try:
                # return self._db_cache['vnc_ports'][fq_name_str]
                raise KeyError
            except KeyError:
                port_obj = self._vnc_lib.virtual_machine_interface_read(
                    fq_name=fq_name, fields=fields)
                self._db_cache['vnc_ports'][fq_name_str] = port_obj
                self._db_cache['vnc_ports'][port_obj.uuid] = port_obj
                return port_obj

    #end _virtual_machine_interface_read

    def _virtual_machine_interface_update(self, port_obj):
        self._vnc_lib.virtual_machine_interface_update(port_obj)
        fq_name_str = json.dumps(port_obj.get_fq_name())

        self._db_cache['vnc_ports'][port_obj.uuid] = port_obj
        self._db_cache['vnc_ports'][fq_name_str] = port_obj
    #end _virtual_machine_interface_update

    def _virtual_machine_interface_delete(self, port_id):
        fq_name_str = None
        try:
            port_obj = self._db_cache['vnc_ports'][port_id]
            fq_name_str = json.dumps(port_obj.get_fq_name())
        except KeyError:
            port_obj = None

        self._vnc_lib.virtual_machine_interface_delete(id=port_id)

        try:
            del self._db_cache['vnc_ports'][port_id]
            if fq_name_str:
                del self._db_cache['vnc_ports'][fq_name_str]
        except KeyError:
            pass
    #end _virtual_machine_interface_delete

    def _virtual_machine_interface_list(self, parent_id=None, back_ref_id=None,
                                        obj_uuids=None, fields=None):
        vmi_objs = self._vnc_lib.virtual_machine_interfaces_list(
                                                     parent_id=parent_id,
                                                     back_ref_id=back_ref_id,
                                                     obj_uuids=obj_uuids,
                                                     detail=True,
                                                     fields=fields)
        return vmi_objs
    #end _virtual_machine_interface_list

    def _instance_ip_create(self, iip_obj):
        iip_uuid = self._vnc_lib.instance_ip_create(iip_obj)

        return iip_uuid
    #end _instance_ip_create

    def _instance_ip_read(self, instance_ip_id=None, fq_name=None):
        if instance_ip_id:
            try:
                # return self._db_cache['vnc_instance_ips'][instance_ip_id]
                raise KeyError
            except KeyError:
                iip_obj = self._vnc_lib.instance_ip_read(id=instance_ip_id)
                fq_name_str = json.dumps(iip_obj.get_fq_name())
                self._db_cache['vnc_instance_ips'][instance_ip_id] = iip_obj
                self._db_cache['vnc_instance_ips'][fq_name_str] = iip_obj
                return iip_obj

        if fq_name:
            fq_name_str = json.dumps(fq_name)
            try:
                # return self._db_cache['vnc_instance_ips'][fq_name_str]
                raise KeyError
            except KeyError:
                iip_obj = self._vnc_lib.instance_ip_read(fq_name=fq_name)
                self._db_cache['vnc_instance_ips'][fq_name_str] = iip_obj
                self._db_cache['vnc_instance_ips'][iip_obj.uuid] = iip_obj
                return iip_obj

    #end _instance_ip_read

    def _instance_ip_update(self, iip_obj):
        self._vnc_lib.instance_ip_update(iip_obj)
        fq_name_str = json.dumps(iip_obj.get_fq_name())

        self._db_cache['vnc_instance_ips'][iip_obj.uuid] = iip_obj
        self._db_cache['vnc_instance_ips'][fq_name_str] = iip_obj
    #end _instance_ip_update

    def _instance_ip_delete(self, instance_ip_id):
        fq_name_str = None
        try:
            iip_obj = self._db_cache['vnc_instance_ips'][instance_ip_id]
            fq_name_str = json.dumps(iip_obj.get_fq_name())
        except KeyError:
            iip_obj = None

        self._vnc_lib.instance_ip_delete(id=instance_ip_id)

        try:
            del self._db_cache['vnc_instance_ips'][instance_ip_id]
            if fq_name_str:
                del self._db_cache['vnc_instance_ips'][fq_name_str]
        except KeyError:
            pass
    #end _instance_ip_delete

    def _instance_ip_list(self, back_ref_id=None, obj_uuids=None, fields=None):
        iip_objs = self._vnc_lib.instance_ips_list(detail=True,
                                                   back_ref_id=back_ref_id,
                                                   obj_uuids=obj_uuids,
                                                   fields=fields)
        return iip_objs
    #end _instance_ip_list

    def _floating_ip_pool_create(self, fip_pool_obj):
        fip_pool_uuid = self._vnc_lib.floating_ip_pool_create(fip_pool_obj)

        return fip_pool_uuid
    # end _floating_ip_pool_create

    def _floating_ip_pool_delete(self, fip_pool_id):
        fip_pool_uuid = self._vnc_lib.floating_ip_pool_delete(id=fip_pool_id)
    # end _floating_ip_pool_delete

    # find projects on a given domain
    def _project_list_domain(self, domain_id):
        # TODO till domain concept is not present in keystone
        fq_name = ['default-domain']
        resp_dict = self._vnc_lib.projects_list(parent_fq_name=fq_name)

        return resp_dict['projects']
    #end _project_list_domain

    # find network ids on a given project
    def _network_list_project(self, project_id, count=False):
        if project_id:
            try:
                project_uuid = str(uuid.UUID(project_id))
            except Exception:
                print "Error in converting uuid %s" % (project_id)
        else:
            project_uuid = None

        if count:
            ret_val = self._virtual_network_list(parent_id=project_uuid,
                                                 count=True)
        else:
            ret_val = self._virtual_network_list(parent_id=project_uuid,
                                                 detail=True)

        return ret_val
    #end _network_list_project

    # find router ids on a given project
    def _router_list_project(self, project_id):
        try:
            project_uuid = str(uuid.UUID(project_id))
        except Exception:
            print "Error in converting uuid %s" % (project_id)

        resp_dict = self._vnc_lib.logical_routers_list(parent_id=project_uuid)

        return resp_dict['logical-routers']
    #end _router_list_project

    def _ipam_list_project(self, project_id):
        try:
            project_uuid = str(uuid.UUID(project_id))
        except Exception:
            print "Error in converting uuid %s" % (project_id)

        resp_dict = self._vnc_lib.network_ipams_list(parent_id=project_uuid)

        return resp_dict['network-ipams']
    #end _ipam_list_project

    def _security_group_list_project(self, project_id):
        if project_id:
            try:
                project_uuid = str(uuid.UUID(project_id))
                # Trigger a project read to ensure project sync
                project_obj = self._project_read(proj_id=project_uuid)
            except Exception:
                print "Error in converting uuid %s" % (project_id)
        else:
            project_uuid = None

        sg_objs = self._vnc_lib.security_groups_list(parent_id=project_uuid,
                                                     detail=True)
        return sg_objs
    #end _security_group_list_project

    def _security_group_entries_list_sg(self, sg_id):
        try:
            sg_uuid = str(uuid.UUID(project_id))
        except Exception:
            print "Error in converting SG uuid %s" % (sg_id)

        resp_dict = self._vnc_lib.security_groups_list(parent_id=project_uuid)

        return resp_dict['security-groups']
    #end _security_group_entries_list_sg

    def _route_table_list_project(self, project_id):
        try:
            project_uuid = str(uuid.UUID(project_id))
        except Exception:
            print "Error in converting uuid %s" % (project_id)

        resp_dict = self._vnc_lib.route_tables_list(parent_id=project_uuid)

        return resp_dict['route-tables']
    #end _route_table_list_project

    def _svc_instance_list_project(self, project_id):
        try:
            project_uuid = str(uuid.UUID(project_id))
        except Exception:
            print "Error in converting uuid %s" % (project_id)

        resp_dict = self._vnc_lib.service_instances_list(parent_id=project_id)

        return resp_dict['service-instances']
    #end _svc_instance_list_project

    def _policy_list_project(self, project_id):
        try:
            project_uuid = str(uuid.UUID(project_id))
        except Exception:
            print "Error in converting uuid %s" % (project_id)

        resp_dict = self._vnc_lib.network_policys_list(parent_id=project_uuid)

        return resp_dict['network-policys']
    #end _policy_list_project

    def _logical_router_read(self, rtr_id=None, fq_name=None):
        if rtr_id:
            try:
                # return self._db_cache['vnc_routers'][rtr_id]
                raise KeyError
            except KeyError:
                rtr_obj = self._vnc_lib.logical_router_read(id=rtr_id)
                fq_name_str = json.dumps(rtr_obj.get_fq_name())
                self._db_cache['vnc_routers'][rtr_id] = rtr_obj
                self._db_cache['vnc_routers'][fq_name_str] = rtr_obj
                return rtr_obj

        if fq_name:
            fq_name_str = json.dumps(fq_name)
            try:
                # return self._db_cache['vnc_routers'][fq_name_str]
                raise KeyError
            except KeyError:
                rtr_obj = self._vnc_lib.logical_router_read(fq_name=fq_name)
                self._db_cache['vnc_routers'][fq_name_str] = rtr_obj
                self._db_cache['vnc_routers'][rtr_obj.uuid] = rtr_obj
                return rtr_obj

    #end _logical_router_read

    def _logical_router_update(self, rtr_obj):
        self._vnc_lib.logical_router_update(rtr_obj)
        fq_name_str = json.dumps(rtr_obj.get_fq_name())

        self._db_cache['vnc_routers'][rtr_obj.uuid] = rtr_obj
        self._db_cache['vnc_routers'][fq_name_str] = rtr_obj
    #end _logical_router_update

    def _logical_router_delete(self, rtr_id):
        fq_name_str = None
        try:
            rtr_obj = self._db_cache['vnc_routers'][rtr_id]
            fq_name_str = json.dumps(rtr_obj.get_fq_name())
        except KeyError:
            pass

        try:
            self._vnc_lib.logical_router_delete(id=rtr_id)
        except RefsExistError:
            self._raise_contrail_exception(409, RouterInUse(router_id=rtr_id))
        try:
            del self._db_cache['vnc_routers'][rtr_id]
            if fq_name_str:
                del self._db_cache['vnc_routers'][fq_name_str]
        except KeyError:
            pass
    #end _logical_router_delete

    def _floatingip_list(self, back_ref_id=None):
        return self._vnc_lib.floating_ips_list(back_ref_id=back_ref_id,
                                               detail=True)
    #end _floatingip_list

    # find floating ip pools a project has access to
    def _fip_pool_refs_project(self, project_id):
        project_obj = self._project_read(proj_id=project_id)

        return project_obj.get_floating_ip_pool_refs()
    #end _fip_pool_refs_project

    def _network_list_shared_and_ext(self):
        ret_list = []
        nets = self._network_list_project(project_id=None)
        for net in nets:
            if net.get_router_external() and net.get_is_shared():
                ret_list.append(net)
        return ret_list
    # end _network_list_router_external

    def _network_list_router_external(self):
        ret_list = []
        nets = self._network_list_project(project_id=None)
        for net in nets:
            if not net.get_router_external():
                continue
            ret_list.append(net)
        return ret_list
    # end _network_list_router_external

    def _network_list_shared(self):
        ret_list = []
        nets = self._network_list_project(project_id=None)
        for net in nets:
            if not net.get_is_shared():
                continue
            ret_list.append(net)
        return ret_list
    # end _network_list_shared

    # find networks of floating ip pools project has access to
    def _fip_pool_ref_networks(self, project_id):
        ret_net_objs = self._network_list_shared()

        proj_fip_pool_refs = self._fip_pool_refs_project(project_id)
        if not proj_fip_pool_refs:
            return ret_net_objs

        for fip_pool_ref in proj_fip_pool_refs:
            fip_uuid = fip_pool_ref['uuid']
            fip_pool_obj = self._vnc_lib.floating_ip_pool_read(id=fip_uuid)
            net_uuid = fip_pool_obj.parent_uuid
            net_obj = self._virtual_network_read(net_id=net_uuid)
            ret_net_objs.append(net_obj)

        return ret_net_objs
    #end _fip_pool_ref_networks

    # find floating ip pools defined by network
    def _fip_pool_list_network(self, net_id):
        resp_dict = self._vnc_lib.floating_ip_pools_list(parent_id=net_id)

        return resp_dict['floating-ip-pools']
    #end _fip_pool_list_network

    def _port_list(self, net_objs, port_objs, iip_objs):
        ret_q_ports = []

        memo_req = {'networks': {},
                    'subnets': {},
                    'instance-ips': {}}

        for net_obj in net_objs:
            # dictionary of iip_uuid to iip_obj
            memo_req['networks'][net_obj.uuid] = net_obj
            subnets_info = self._virtual_network_to_subnets(net_obj)
            memo_req['subnets'][net_obj.uuid] = subnets_info

        for iip_obj in iip_objs:
            # dictionary of iip_uuid to iip_obj
            memo_req['instance-ips'][iip_obj.uuid] = iip_obj

        for port_obj in port_objs:
            port_info = self._port_vnc_to_neutron(port_obj, memo_req)
            ret_q_ports.append(port_info)

        return ret_q_ports
    #end _port_list

    def _port_list_network(self, network_ids, count=False):
        ret_list = []
        net_objs = self._virtual_network_list(obj_uuids=network_ids,
                         fields=['virtual_machine_interface_back_refs'],
                         detail=True)
        if not net_objs:
            return ret_list

        net_ids = [net_obj.uuid for net_obj in net_objs]
        port_objs = self._virtual_machine_interface_list(back_ref_id=net_ids,
                                          fields=['instance_ip_back_refs'])
        iip_objs = self._instance_ip_list(back_ref_id=net_ids)

        return self._port_list(net_objs, port_objs, iip_objs)
    #end _port_list_network

    # find port ids on a given project
    def _port_list_project(self, project_id, count=False):
        if count:
            ret_val = 0
        else:
            ret_val = []

        net_objs = self._virtual_network_list(project_id,
                         fields=['virtual_machine_interface_back_refs'],
                         detail=True)
        if not net_objs:
            return ret_val

        if count:
            for net_obj in net_objs:
                port_back_refs = (
                    net_obj.get_virtual_machine_interface_back_refs() or [])
                ret_val = ret_val + len(port_back_refs)
            return ret_val

        net_ids = [net_obj.uuid for net_obj in net_objs]
        port_objs = self._virtual_machine_interface_list(back_ref_id=net_ids,
                                          fields=['instance_ip_back_refs'])
        iip_objs = self._instance_ip_list(back_ref_id=net_ids)

        ret_val = self._port_list(net_objs, port_objs, iip_objs)

        return ret_val
    #end _port_list_project

    # Returns True if
    #     * no filter is specified
    #     OR
    #     * search-param is not present in filters
    #     OR
    #     * 1. search-param is present in filters AND
    #       2. resource matches param-list AND
    #       3. shared parameter in filters is False
    def _filters_is_present(self, filters, key_name, match_value):
        if filters:
            if key_name in filters:
                try:
                    if key_name == 'tenant_id':
                        filter_value = [str(uuid.UUID(t_id)) \
                                        for t_id in filters[key_name]]
                    else:
                        filter_value = filters[key_name]
                    idx = filter_value.index(match_value)
                except ValueError:  # not in requested list
                    return False
        return True
    #end _filters_is_present

    def _network_read(self, net_uuid):
        net_obj = self._virtual_network_read(net_id=net_uuid)
        return net_obj
    #end _network_read

    def _subnet_vnc_create_mapping(self, subnet_id, subnet_key):
        self._vnc_lib.kv_store(subnet_id, subnet_key)
        self._vnc_lib.kv_store(subnet_key, subnet_id)
        self._db_cache['q_subnet_maps'][subnet_id] = subnet_key
        self._db_cache['q_subnet_maps'][subnet_key] = subnet_id
    #end _subnet_vnc_create_mapping

    def _subnet_vnc_read_mapping(self, id=None, key=None):
        if id:
            try:
                subnet_key = self._vnc_lib.kv_retrieve(id)
                self._db_cache['q_subnet_maps'][id] = subnet_key
                return subnet_key
            except NoIdError:
                self._raise_contrail_exception(404, exceptions.SubnetNotFound(subnet_id=id))

        if key:
            subnet_id = self._vnc_lib.kv_retrieve(key)
            self._db_cache['q_subnet_maps'][key] = subnet_id
            return subnet_id

    #end _subnet_vnc_read_mapping

    def _subnet_vnc_read_or_create_mapping(self, id=None, key=None):
        if id:
            return self._subnet_vnc_read_mapping(id=id)

        # if subnet was created outside of neutron handle it and create
        # neutron representation now (lazily)
        try:
            return self._subnet_vnc_read_mapping(key=key)
        except NoIdError:
            subnet_id = str(uuid.uuid4())
            self._subnet_vnc_create_mapping(subnet_id, key)
            return self._subnet_vnc_read_mapping(key=key)
    #end _subnet_vnc_read_or_create_mapping

    def _subnet_vnc_delete_mapping(self, subnet_id, subnet_key):
        self._vnc_lib.kv_delete(subnet_id)
        self._vnc_lib.kv_delete(subnet_key)
        try:
            del self._db_cache['q_subnet_maps'][subnet_id]
            del self._db_cache['q_subnet_maps'][subnet_key]
        except KeyError:
            pass
    #end _subnet_vnc_delete_mapping

    def _subnet_vnc_get_key(self, subnet_vnc, net_id):
        pfx = subnet_vnc.subnet.get_ip_prefix()
        pfx_len = subnet_vnc.subnet.get_ip_prefix_len()

        network = IPNetwork('%s/%s' % (pfx, pfx_len))
        return '%s %s/%s' % (net_id, str(network.ip), pfx_len)
    #end _subnet_vnc_get_key

    def _subnet_read(self, net_uuid, subnet_key):
        try:
            net_obj = self._virtual_network_read(net_id=net_uuid)
        except NoIdError:
            return None

        ipam_refs = net_obj.get_network_ipam_refs()
        if not ipam_refs:
            return None

        # TODO scope for optimization
        for ipam_ref in ipam_refs:
            subnet_vncs = ipam_ref['attr'].get_ipam_subnets()
            for subnet_vnc in subnet_vncs:
                if self._subnet_vnc_get_key(subnet_vnc,
                                            net_uuid) == subnet_key:
                    return subnet_vnc

        return None
    #end _subnet_read

    def _ip_address_to_subnet_id(self, ip_addr, net_obj):
        # find subnet-id for ip-addr, called when instance-ip created
        ipam_refs = net_obj.get_network_ipam_refs()
        if ipam_refs:
            for ipam_ref in ipam_refs:
                subnet_vncs = ipam_ref['attr'].get_ipam_subnets()
                for subnet_vnc in subnet_vncs:
                    cidr = '%s/%s' % (subnet_vnc.subnet.get_ip_prefix(),
                                      subnet_vnc.subnet.get_ip_prefix_len())
                    if IPAddress(ip_addr) in IPSet([cidr]):
                        subnet_key = self._subnet_vnc_get_key(subnet_vnc,
                                                              net_obj.uuid)
                        subnet_id = self._subnet_vnc_read_or_create_mapping(
                            key=subnet_key)
                        return subnet_id

        return None
    #end _ip_address_to_subnet_id

    # Returns a list of dicts of subnet-id:cidr for a VN
    def _virtual_network_to_subnets(self, net_obj):
        ret_subnets = []

        ipam_refs = net_obj.get_network_ipam_refs()
        if ipam_refs:
            for ipam_ref in ipam_refs:
                subnet_vncs = ipam_ref['attr'].get_ipam_subnets()
                for subnet_vnc in subnet_vncs:
                    subnet_key = self._subnet_vnc_get_key(subnet_vnc,
                                                          net_obj.uuid)
                    subnet_id = self._subnet_vnc_read_or_create_mapping(
                        key=subnet_key)
                    cidr = '%s/%s' % (subnet_vnc.subnet.get_ip_prefix(),
                                      subnet_vnc.subnet.get_ip_prefix_len())
                    ret_subnets.append({'id': subnet_id, 'cidr': cidr})

        return ret_subnets
    # end _virtual_network_to_subnets

    # Conversion routines between VNC and Quantum objects
    def _svc_instance_neutron_to_vnc(self, si_q, oper):
        if oper == CREATE:
            project_id = str(uuid.UUID(si_q['tenant_id']))
            project_obj = self._project_read(proj_id=project_id)
            net_id = si_q['external_net']
            ext_vn = self._vnc_lib.virtual_network_read(id=net_id)
            scale_out = ServiceScaleOutType(max_instances=1, auto_scale=False)
            si_prop = ServiceInstanceType(
                      auto_policy=True,
                      left_virtual_network="",
                      right_virtual_network=ext_vn.get_fq_name_str(),
                      scale_out=scale_out)
            si_prop.set_scale_out(scale_out)
            si_vnc = ServiceInstance(name=si_q['name'],
                         parent_obj=project_obj,
                         service_instance_properties=si_prop)

        return si_vnc
    #end _svc_instance_neutron_to_vnc

    def _svc_instance_vnc_to_neutron(self, si_obj):
        si_q_dict = self._obj_to_dict(si_obj)

        # replace field names
        si_q_dict['id'] = si_obj.uuid
        si_q_dict['tenant_id'] = si_obj.parent_uuid.replace('-', '')
        si_q_dict['name'] = si_obj.name
        si_props = si_obj.get_service_instance_properties()
        if si_props:
            vn_fq_name = si_props.get_right_virtual_network()
            vn_obj = self._vnc_lib.virtual_network_read(fq_name_str=vn_fq_name)
            si_q_dict['external_net'] = str(vn_obj.uuid) + ' ' + vn_obj.name
            si_q_dict['internal_net'] = ''

        return si_q_dict
    #end _route_table_vnc_to_neutron

    def _route_table_neutron_to_vnc(self, rt_q, oper):
        if oper == CREATE:
            project_id = str(uuid.UUID(rt_q['tenant_id']))
            project_obj = self._project_read(proj_id=project_id)
            rt_vnc = RouteTable(name=rt_q['name'],
                                parent_obj=project_obj)

            if not rt_q['routes']:
                return rt_vnc
            for route in rt_q['routes']['route']:
                try:
                    vm_obj = self._vnc_lib.virtual_machine_read(
                        id=route['next_hop'])
                    si_list = vm_obj.get_service_instance_refs()
                    if si_list:
                        fq_name = si_list[0]['to']
                        si_obj = self._vnc_lib.service_instance_read(
                            fq_name=fq_name)
                        route['next_hop'] = si_obj.get_fq_name_str()
                except Exception as e:
                    pass
            rt_vnc.set_routes(RouteTableType.factory(**rt_q['routes']))
        else:
            rt_vnc = self._vnc_lib.route_table_read(id=rt_q['id'])

            for route in rt_q['routes']['route']:
                try:
                    vm_obj = self._vnc_lib.virtual_machine_read(
                        id=route['next_hop'])
                    si_list = vm_obj.get_service_instance_refs()
                    if si_list:
                        fq_name = si_list[0]['to']
                        si_obj = self._vnc_lib.service_instance_read(
                            fq_name=fq_name)
                        route['next_hop'] = si_obj.get_fq_name_str()
                except Exception as e:
                    pass
            rt_vnc.set_routes(RouteTableType.factory(**rt_q['routes']))

        return rt_vnc
    #end _route_table_neutron_to_vnc

    def _route_table_vnc_to_neutron(self, rt_obj):
        rt_q_dict = self._obj_to_dict(rt_obj)

        # replace field names
        rt_q_dict['id'] = rt_obj.uuid
        rt_q_dict['tenant_id'] = rt_obj.parent_uuid.replace('-', '')
        rt_q_dict['name'] = rt_obj.name
        rt_q_dict['fq_name'] = rt_obj.fq_name

        # get route table routes
        rt_q_dict['routes'] = rt_q_dict.pop('routes', None)
        if rt_q_dict['routes']:
            for route in rt_q_dict['routes']['route']:
                if route['next_hop_type']:
                    route['next_hop'] = route['next_hop_type']

        return rt_q_dict
    #end _route_table_vnc_to_neutron

    def _security_group_vnc_to_neutron(self, sg_obj):
        sg_q_dict = {}
        extra_dict = {}
        extra_dict['contrail:fq_name'] = sg_obj.get_fq_name()

        # replace field names
        sg_q_dict['id'] = sg_obj.uuid
        sg_q_dict['tenant_id'] = sg_obj.parent_uuid.replace('-', '')
        if not sg_obj.display_name:
            # for security groups created directly via vnc_api
            sg_q_dict['name'] = sg_obj.get_fq_name()[-1]
        else:
            sg_q_dict['name'] = sg_obj.display_name
        sg_q_dict['description'] = sg_obj.get_id_perms().get_description()

        # get security group rules
        sg_q_dict['security_group_rules'] = []
        rule_list = self.security_group_rules_read(sg_obj.uuid, sg_obj)
        if rule_list:
            for rule in rule_list:
                sg_q_dict['security_group_rules'].append(rule)

        if self._contrail_extensions_enabled:
            sg_q_dict.update(extra_dict)
        return sg_q_dict
    #end _security_group_vnc_to_neutron

    def _security_group_neutron_to_vnc(self, sg_q, oper):
        if oper == CREATE:
            project_id = str(uuid.UUID(sg_q['tenant_id']))
            def project_read(id):
                try:
                    return self._project_read(proj_id=id)
                except NoIdError:
                    return None
            for i in range(10):
                project_obj = project_read(project_id)
                if project_obj:
                    break
                gevent.sleep(2)
            id_perms = IdPermsType(enable=True,
                                   description=sg_q.get('description'))
            sg_vnc = SecurityGroup(name=sg_q['name'],
                                   parent_obj=project_obj,
                                   id_perms=id_perms)
        else:
            sg_vnc = self._vnc_lib.security_group_read(id=sg_q['id'])

        if 'name' in sg_q and sg_q['name']:
	    sg_vnc.display_name = sg_q['name']
        if 'description' in sg_q:
            id_perms = sg_vnc.get_id_perms()
            id_perms.set_description(sg_q['description'])
            sg_vnc.set_id_perms(id_perms)
        return sg_vnc
    #end _security_group_neutron_to_vnc

    def _security_group_rule_vnc_to_neutron(self, sg_id, sg_rule, sg_obj=None):
        sgr_q_dict = {}
        if sg_id == None:
            return sgr_q_dict

        if not sg_obj:
            try:
                sg_obj = self._vnc_lib.security_group_read(id=sg_id)
            except NoIdError:
                self._raise_contrail_exception(404, SecurityGroupNotFound(id=sg_id))

        remote_cidr = None
        remote_sg_uuid = None
        saddr = sg_rule.get_src_addresses()[0]
        daddr = sg_rule.get_dst_addresses()[0]
        if saddr.get_security_group() == 'local':
            direction = 'egress'
            addr = daddr
        elif daddr.get_security_group() == 'local':
            direction = 'ingress'
            addr = saddr
        else:
            self._raise_contrail_exception(404, SecurityGroupRuleNotFound(id=sg_rule.get_rule_uuid()))

        if addr.get_subnet():
            remote_cidr = '%s/%s' % (addr.get_subnet().get_ip_prefix(),
                                     addr.get_subnet().get_ip_prefix_len())
        elif addr.get_security_group():
            if addr.get_security_group() != 'any' and \
                addr.get_security_group() != 'local':
                remote_sg = addr.get_security_group()
                try:
                    remote_sg_obj = self._vnc_lib.security_group_read(
                        fq_name_str=remote_sg)
                    remote_sg_uuid = remote_sg_obj.uuid
                except NoIdError:
                    pass

        sgr_q_dict['id'] = sg_rule.get_rule_uuid()
        sgr_q_dict['tenant_id'] = sg_obj.parent_uuid.replace('-', '')
        sgr_q_dict['security_group_id'] = sg_obj.uuid
        sgr_q_dict['ethertype'] = 'IPv4'
        sgr_q_dict['direction'] = direction
        sgr_q_dict['protocol'] = sg_rule.get_protocol()
        sgr_q_dict['port_range_min'] = sg_rule.get_dst_ports()[0].\
            get_start_port()
        sgr_q_dict['port_range_max'] = sg_rule.get_dst_ports()[0].\
            get_end_port()
        sgr_q_dict['remote_ip_prefix'] = remote_cidr
        sgr_q_dict['remote_group_id'] = remote_sg_uuid

        return sgr_q_dict
    #end _security_group_rule_vnc_to_neutron

    def _security_group_rule_neutron_to_vnc(self, sgr_q, oper):
        if oper == CREATE:
            port_min = 0
            port_max = 65535
            if sgr_q['port_range_min']:
                port_min = sgr_q['port_range_min']
            if sgr_q['port_range_max']:
                port_max = sgr_q['port_range_max']

            endpt = [AddressType(security_group='any')]
            if sgr_q['remote_ip_prefix']:
                cidr = sgr_q['remote_ip_prefix'].split('/')
                pfx = cidr[0]
                pfx_len = int(cidr[1])
                endpt = [AddressType(subnet=SubnetType(pfx, pfx_len))]
            elif sgr_q['remote_group_id']:
                sg_obj = self._vnc_lib.security_group_read(
                    id=sgr_q['remote_group_id'])
                endpt = [AddressType(security_group=sg_obj.get_fq_name_str())]

            if sgr_q['direction'] == 'ingress':
                dir = '>'
                local = endpt
                remote = [AddressType(security_group='local')]
            else:
                dir = '>'
                remote = endpt
                local = [AddressType(security_group='local')]

            if not sgr_q['protocol']:
                sgr_q['protocol'] = 'any'

            sgr_uuid = str(uuid.uuid4())

            rule = PolicyRuleType(rule_uuid=sgr_uuid, direction=dir,
                                  protocol=sgr_q['protocol'],
                                  src_addresses=local,
                                  src_ports=[PortType(0, 65535)],
                                  dst_addresses=remote,
                                  dst_ports=[PortType(port_min, port_max)])
            return rule
    #end _security_group_rule_neutron_to_vnc

    def _network_neutron_to_vnc(self, network_q, oper):
        net_name = network_q.get('name', None)
        try:
            external_attr = network_q['router:external']
        except KeyError:
            external_attr = attr.ATTR_NOT_SPECIFIED
        if oper == CREATE:
            project_id = str(uuid.UUID(network_q['tenant_id']))
            def project_read(id):
                try:
                    return self._project_read(proj_id=id)
                except NoIdError:
                    return None
            for i in range(10):
                project_obj = project_read(project_id)
                if project_obj:
                    break
                gevent.sleep(2)

            id_perms = IdPermsType(enable=True)
            net_obj = VirtualNetwork(net_name, project_obj, id_perms=id_perms)
            if external_attr == attr.ATTR_NOT_SPECIFIED:
                net_obj.router_external = False
            else:
                net_obj.router_external = external_attr
            if 'shared' in network_q:
                net_obj.is_shared = network_q['shared']
            else:
                net_obj.is_shared = False
        else:  # READ/UPDATE/DELETE
            net_obj = self._virtual_network_read(net_id=network_q['id'])
            if oper == UPDATE:
                if 'shared' in network_q:
                    net_obj.is_shared = network_q['shared']
                if external_attr is not attr.ATTR_NOT_SPECIFIED:
                    net_obj.router_external = external_attr

        if 'name' in network_q and network_q['name']:
            net_obj.display_name = network_q['name']

        id_perms = net_obj.get_id_perms()
        if 'admin_state_up' in network_q:
            id_perms.enable = network_q['admin_state_up']
            net_obj.set_id_perms(id_perms)

        if 'contrail:policys' in network_q:
            policy_fq_names = network_q['contrail:policys']
            # reset and add with newly specified list
            net_obj.set_network_policy_list([], [])
            seq = 0
            for p_fq_name in policy_fq_names:
                domain_name, project_name, policy_name = p_fq_name

                domain_obj = Domain(domain_name)
                project_obj = Project(project_name, domain_obj)
                policy_obj = NetworkPolicy(policy_name, project_obj)

                net_obj.add_network_policy(policy_obj,
                                           VirtualNetworkPolicyType(
                                           sequence=SequenceType(seq, 0)))
                seq = seq + 1

        if 'vpc:route_table' in network_q:
            rt_fq_name = network_q['vpc:route_table']
            if rt_fq_name:
                try:
                    rt_obj = self._vnc_lib.route_table_read(fq_name=rt_fq_name)
                    net_obj.set_route_table(rt_obj)
                except NoIdError:
                    # TODO add route table specific exception
                    self._raise_contrail_exception(404, exceptions.NetworkNotFound(net_id=net_obj.uuid))

        return net_obj
    #end _network_neutron_to_vnc

    def _network_vnc_to_neutron(self, net_obj, net_repr='SHOW'):
        net_q_dict = {}
        extra_dict = {}

        id_perms = net_obj.get_id_perms()
        perms = id_perms.permissions
        net_q_dict['id'] = net_obj.uuid

        if not net_obj.display_name:
            # for nets created directly via vnc_api
            net_q_dict['name'] = net_obj.get_fq_name()[-1]
        else:
            net_q_dict['name'] = net_obj.display_name

        extra_dict['contrail:fq_name'] = net_obj.get_fq_name()
        net_q_dict['tenant_id'] = net_obj.parent_uuid.replace('-', '')
        net_q_dict['admin_state_up'] = id_perms.enable
        if net_obj.is_shared:
            net_q_dict['shared'] = True
        else:
            net_q_dict['shared'] = False
        net_q_dict['status'] = (constants.NET_STATUS_ACTIVE if id_perms.enable
                                else constants.NET_STATUS_DOWN)
        if net_obj.router_external:
            net_q_dict['router:external'] = True
        else:
            net_q_dict['router:external'] = False

        if net_repr == 'SHOW' or net_repr == 'LIST':
            extra_dict['contrail:instance_count'] = 0

            net_policy_refs = net_obj.get_network_policy_refs()
            if net_policy_refs:
                sorted_refs = sorted(
                    net_policy_refs,
                    key=lambda t:(t['attr'].sequence.major,
                                  t['attr'].sequence.minor))
                extra_dict['contrail:policys'] = \
                    [np_ref['to'] for np_ref in sorted_refs]

        rt_refs = net_obj.get_route_table_refs()
        if rt_refs:
            extra_dict['vpc:route_table'] = \
                [rt_ref['to'] for rt_ref in rt_refs]

        ipam_refs = net_obj.get_network_ipam_refs()
        net_q_dict['subnets'] = []
        if ipam_refs:
            extra_dict['contrail:subnet_ipam'] = []
            for ipam_ref in ipam_refs:
                subnets = ipam_ref['attr'].get_ipam_subnets()
                for subnet in subnets:
                    sn_dict = self._subnet_vnc_to_neutron(subnet, net_obj,
                                                          ipam_ref['to'])
                    net_q_dict['subnets'].append(sn_dict['id'])
                    sn_ipam = {}
                    sn_ipam['subnet_cidr'] = sn_dict['cidr']
                    sn_ipam['ipam_fq_name'] = ipam_ref['to']
                    extra_dict['contrail:subnet_ipam'].append(sn_ipam)

        if self._contrail_extensions_enabled:
            net_q_dict.update(extra_dict)

        return net_q_dict
    #end _network_vnc_to_neutron

    def _subnet_neutron_to_vnc(self, subnet_q):
        cidr = IPNetwork(subnet_q['cidr'])
        pfx = str(cidr.network)
        pfx_len = int(cidr.prefixlen)
        if cidr.version != 4:
            exc_info = {'type': 'BadRequest',
                        'message': "Bad subnet request: IPv6 is not supported"}
            bottle.abort(400, json.dumps(exc_info))

        if 'gateway_ip' in subnet_q:
            default_gw = subnet_q['gateway_ip']
        else:
            # Assigned first+1 from cidr
            default_gw = str(IPAddress(cidr.first + 1))

        if 'allocation_pools' in subnet_q:
            alloc_pools = subnet_q['allocation_pools']
        else:
            # Assigned by address manager
            alloc_pools = None

        dhcp_option_list = None
        if 'dns_nameservers' in subnet_q and subnet_q['dns_nameservers']:
            dhcp_options=[]
            for dns_server in subnet_q['dns_nameservers']:
                dhcp_options.append(DhcpOptionType(dhcp_option_name='6',
                                                   dhcp_option_value=dns_server))
            if dhcp_options:
                dhcp_option_list = DhcpOptionsListType(dhcp_options)

        host_route_list = None
        if 'host_routes' in subnet_q and subnet_q['host_routes']:
            host_routes=[]
            for host_route in subnet_q['host_routes']:
                host_routes.append(RouteType(prefix=host_route['destination'],
                                             next_hop=host_route['nexthop']))
            if host_routes:
                host_route_list = RouteTableType(host_routes)

        if 'enable_dhcp' in subnet_q:
            dhcp_config = subnet_q['enable_dhcp']
        else:
            dhcp_config = None
        sn_name=subnet_q.get('name')
        subnet_vnc = IpamSubnetType(subnet=SubnetType(pfx, pfx_len),
                                    default_gateway=default_gw,
                                    enable_dhcp=dhcp_config,
                                    dns_nameservers=None,
                                    allocation_pools=alloc_pools,
                                    addr_from_start=True,
                                    dhcp_option_list=dhcp_option_list,
                                    host_routes=host_route_list,
                                    subnet_name=sn_name)

        return subnet_vnc
    #end _subnet_neutron_to_vnc

    def _subnet_vnc_to_neutron(self, subnet_vnc, net_obj, ipam_fq_name):
        sn_q_dict = {}
        sn_name = subnet_vnc.get_subnet_name()
        if sn_name is not None:
            sn_q_dict['name'] = sn_name
        else:
            sn_q_dict['name'] = ''
        sn_q_dict['tenant_id'] = net_obj.parent_uuid.replace('-', '')
        sn_q_dict['network_id'] = net_obj.uuid
        sn_q_dict['ip_version'] = 4  # TODO ipv6?
        sn_q_dict['ipv6_ra_mode'] = None
        sn_q_dict['ipv6_address_mode'] = None

        cidr = '%s/%s' % (subnet_vnc.subnet.get_ip_prefix(),
                          subnet_vnc.subnet.get_ip_prefix_len())
        sn_q_dict['cidr'] = cidr

        subnet_key = self._subnet_vnc_get_key(subnet_vnc, net_obj.uuid)
        sn_id = self._subnet_vnc_read_or_create_mapping(key=subnet_key)

        sn_q_dict['id'] = sn_id

        sn_q_dict['gateway_ip'] = subnet_vnc.default_gateway

        alloc_obj_list = subnet_vnc.get_allocation_pools()
        allocation_pools = []
        for alloc_obj in alloc_obj_list:
            first_ip = alloc_obj.get_start()
            last_ip = alloc_obj.get_end()
            alloc_dict = {'first_ip':first_ip, 'last_ip':last_ip}
            allocation_pools.append(alloc_dict)

        if allocation_pools is None or not allocation_pools:
            if (int(IPNetwork(sn_q_dict['gateway_ip']).network) ==
                int(IPNetwork(cidr).network+1)):
                first_ip = str(IPNetwork(cidr).network + 2)
            else:
                first_ip = str(IPNetwork(cidr).network + 1)
            last_ip = str(IPNetwork(cidr).broadcast - 1)
            cidr_pool = {'first_ip':first_ip, 'last_ip':last_ip}
            allocation_pools.append(cidr_pool)
        sn_q_dict['allocation_pools'] = allocation_pools

        sn_q_dict['enable_dhcp'] = subnet_vnc.get_enable_dhcp()

        nameserver_dict_list = list()
        dhcp_option_list = subnet_vnc.get_dhcp_option_list()
        if dhcp_option_list:
            for dhcp_option in dhcp_option_list.dhcp_option:
                if dhcp_option.get_dhcp_option_name() == '6':
                    nameserver_entry = {'address': dhcp_option.get_dhcp_option_value(),
                                        'subnet_id': sn_id}
                    nameserver_dict_list.append(nameserver_entry)
        sn_q_dict['dns_nameservers'] = nameserver_dict_list

        host_route_dict_list = list()
        host_routes = subnet_vnc.get_host_routes()
        if host_routes:
            for host_route in host_routes.route:
                host_route_entry = {'destination': host_route.get_prefix(),
                                    'nexthop': host_route.get_next_hop(),
                                    'subnet_id': sn_id}
                host_route_dict_list.append(host_route_entry)
        sn_q_dict['routes'] = host_route_dict_list

        if net_obj.is_shared:
            sn_q_dict['shared'] = True
        else:
            sn_q_dict['shared'] = False

        return sn_q_dict
    #end _subnet_vnc_to_neutron

    def _ipam_neutron_to_vnc(self, ipam_q, oper):
        ipam_name = ipam_q.get('name', None)
        if oper == CREATE:
            project_id = str(uuid.UUID(ipam_q['tenant_id']))
            project_obj = self._project_read(proj_id=project_id)
            ipam_obj = NetworkIpam(ipam_name, project_obj)
        else:  # READ/UPDATE/DELETE
            ipam_obj = self._vnc_lib.network_ipam_read(id=ipam_q['id'])

        options_vnc = DhcpOptionsListType()
        if ipam_q['mgmt']:
            #for opt_q in ipam_q['mgmt'].get('options', []):
            #    options_vnc.add_dhcp_option(DhcpOptionType(opt_q['option'],
            #                                               opt_q['value']))
            #ipam_mgmt_vnc = IpamType.factory(
            #                    ipam_method = ipam_q['mgmt']['method'],
            #                                 dhcp_option_list = options_vnc)
            ipam_obj.set_network_ipam_mgmt(IpamType.factory(**ipam_q['mgmt']))

        return ipam_obj
    #end _ipam_neutron_to_vnc

    def _ipam_vnc_to_neutron(self, ipam_obj):
        ipam_q_dict = self._obj_to_dict(ipam_obj)

        # replace field names
        ipam_q_dict['id'] = ipam_q_dict.pop('uuid')
        ipam_q_dict['name'] = ipam_obj.name
        ipam_q_dict['tenant_id'] = ipam_obj.parent_uuid.replace('-', '')
        ipam_q_dict['mgmt'] = ipam_q_dict.pop('network_ipam_mgmt', None)
        net_back_refs = ipam_q_dict.pop('virtual_network_back_refs', None)
        if net_back_refs:
            ipam_q_dict['nets_using'] = []
            for net_back_ref in net_back_refs:
                net_fq_name = net_back_ref['to']
                ipam_q_dict['nets_using'].append(net_fq_name)

        return ipam_q_dict
    #end _ipam_vnc_to_neutron

    def _policy_neutron_to_vnc(self, policy_q, oper):
        policy_name = policy_q.get('name', None)
        if oper == CREATE:
            project_id = str(uuid.UUID(policy_q['tenant_id']))
            project_obj = self._project_read(proj_id=project_id)
            policy_obj = NetworkPolicy(policy_name, project_obj)
        else:  # READ/UPDATE/DELETE
            policy_obj = self._vnc_lib.network_policy_read(id=policy_q['id'])

        policy_obj.set_network_policy_entries(
            PolicyEntriesType.factory(**policy_q['entries']))

        return policy_obj
    #end _policy_neutron_to_vnc

    def _policy_vnc_to_neutron(self, policy_obj):
        policy_q_dict = self._obj_to_dict(policy_obj)

        # replace field names
        policy_q_dict['id'] = policy_q_dict.pop('uuid')
        policy_q_dict['name'] = policy_obj.name
        policy_q_dict['tenant_id'] = policy_obj.parent_uuid.replace('-', '')
        policy_q_dict['entries'] = policy_q_dict.pop('network_policy_entries',
                                                     None)
        net_back_refs = policy_obj.get_virtual_network_back_refs()
        if net_back_refs:
            policy_q_dict['nets_using'] = []
            for net_back_ref in net_back_refs:
                net_fq_name = net_back_ref['to']
                policy_q_dict['nets_using'].append(net_fq_name)

        return policy_q_dict
    #end _policy_vnc_to_neutron

    def _router_neutron_to_vnc(self, router_q, oper):
        rtr_name = router_q.get('name', None)
        if oper == CREATE:
            project_id = str(uuid.UUID(router_q['tenant_id']))
            def project_read(id):
                try:
                    return self._project_read(proj_id=id)
                except NoIdError:
                    return None
            for i in range(10):
                project_obj = project_read(project_id)
                if project_obj:
                    break
                gevent.sleep(2)
            id_perms = IdPermsType(enable=True)
            rtr_obj = LogicalRouter(rtr_name, project_obj, id_perms=id_perms)
        else:  # READ/UPDATE/DELETE
            rtr_obj = self._logical_router_read(rtr_id=router_q['id'])

        id_perms = rtr_obj.get_id_perms()
        if 'admin_state_up' in router_q:
            id_perms.enable = router_q['admin_state_up']
            rtr_obj.set_id_perms(id_perms)

        if 'name' in router_q and router_q['name']:
            rtr_obj.display_name = router_q['name']

        return rtr_obj
    #end _router_neutron_to_vnc

    def _router_vnc_to_neutron(self, rtr_obj, rtr_repr='SHOW'):
        rtr_q_dict = {}
        extra_dict = {}
        extra_dict['contrail:fq_name'] = rtr_obj.get_fq_name()

        rtr_q_dict['id'] = rtr_obj.uuid
        if not rtr_obj.display_name:
            rtr_q_dict['name'] = rtr_obj.get_fq_name()[-1]
        else:
            rtr_q_dict['name'] = rtr_obj.display_name
        rtr_q_dict['tenant_id'] = rtr_obj.parent_uuid.replace('-', '')
        rtr_q_dict['admin_state_up'] = rtr_obj.get_id_perms().enable
        rtr_q_dict['shared'] = False
        rtr_q_dict['status'] = constants.NET_STATUS_ACTIVE
        rtr_q_dict['gw_port_id'] = None
        rtr_q_dict['external_gateway_info'] = None
        vn_refs = rtr_obj.get_virtual_network_refs()
        if vn_refs:
            rtr_q_dict['external_gateway_info'] = {'network_id':
                                                   vn_refs[0]['uuid']}
        if self._contrail_extensions_enabled:
            rtr_q_dict.update(extra_dict)
        return rtr_q_dict
    #end _router_vnc_to_neutron

    def _floatingip_neutron_to_vnc(self, fip_q, oper):
        if oper == CREATE:
            # TODO for now create from default pool, later
            # use first available pool on net
            net_id = fip_q['floating_network_id']
            try:
                fq_name = self._fip_pool_list_network(net_id)[0]['fq_name']
            except IndexError:
                # IndexError could happens when an attempt to
                # retrieve a floating ip pool from a private network.
                self._raise_contrail_exception(404, Exception(
                    "Network %s doesn't provide a floatingip pool", net_id))
            fq_name = self._fip_pool_list_network(net_id)[0]['fq_name']
            fip_pool_obj = self._vnc_lib.floating_ip_pool_read(fq_name=fq_name)
            fip_name = str(uuid.uuid4())
            fip_obj = FloatingIp(fip_name, fip_pool_obj)
            fip_obj.uuid = fip_name

            proj_id = str(uuid.UUID(fip_q['tenant_id']))
            proj_obj = self._project_read(proj_id=proj_id)
            fip_obj.set_project(proj_obj)
        else:  # READ/UPDATE/DELETE
            fip_obj = self._vnc_lib.floating_ip_read(id=fip_q['id'])

        if fip_q.get('port_id'):
            port_obj = self._virtual_machine_interface_read(
                port_id=fip_q['port_id'])
            fip_obj.set_virtual_machine_interface(port_obj)
        else:
            fip_obj.set_virtual_machine_interface_list([])

        if fip_q.get('fixed_ip_address'):
            fip_obj.set_floating_ip_fixed_ip_address(fip_q['fixed_ip_address'])
        else:
            # fixed_ip_address not specified, pick from port_obj in create,
            # reset in case of disassociate
            port_refs = fip_obj.get_virtual_machine_interface_refs()
            if not port_refs:
                fip_obj.set_floating_ip_fixed_ip_address(None)
            else:
                port_obj = self._virtual_machine_interface_read(
                    port_id=port_refs[0]['uuid'], fields=['instance_ip_back_refs'])
                iip_refs = port_obj.get_instance_ip_back_refs()
                if iip_refs:
                    iip_obj = self._instance_ip_read(instance_ip_id=iip_refs[0]['uuid'])
                    fip_obj.set_floating_ip_fixed_ip_address(iip_obj.get_instance_ip_address())

        return fip_obj
    #end _floatingip_neutron_to_vnc

    def _floatingip_vnc_to_neutron(self, fip_obj):
        fip_q_dict = {}

        floating_net_id = self._vnc_lib.fq_name_to_id('virtual-network',
                                             fip_obj.get_fq_name()[:-2])
        tenant_id = fip_obj.get_project_refs()[0]['uuid'].replace('-', '')

        port_id = None
        fixed_ip = None
        router_id = None
        port_refs = fip_obj.get_virtual_machine_interface_refs()
        if port_refs:
            port_id = port_refs[0]['uuid']
            port_obj = self._virtual_machine_interface_read(port_id=port_id,
                                             fields=['instance_ip_back_refs'])

            # find router_id from port
            internal_net_obj = self._virtual_network_read(net_id=port_obj.get_virtual_network_refs()[0]['uuid'])
            net_port_objs = [self._virtual_machine_interface_read(port_id=port['uuid']) for port in internal_net_obj.get_virtual_machine_interface_back_refs()]
            for net_port_obj in net_port_objs:
                routers = net_port_obj.get_logical_router_back_refs()
                if routers:
                    router_id = routers[0]['uuid']
                    break

        fip_q_dict['id'] = fip_obj.uuid
        fip_q_dict['tenant_id'] = tenant_id
        fip_q_dict['floating_ip_address'] = fip_obj.get_floating_ip_address()
        fip_q_dict['floating_network_id'] = floating_net_id
        fip_q_dict['router_id'] = router_id
        fip_q_dict['port_id'] = port_id
        fip_q_dict['fixed_ip_address'] = fip_obj.get_floating_ip_fixed_ip_address()
        fip_q_dict['status'] = constants.PORT_STATUS_ACTIVE

        return fip_q_dict
    #end _floatingip_vnc_to_neutron

    def _port_neutron_to_vnc(self, port_q, net_obj, oper):
        if oper == CREATE:
            project_id = str(uuid.UUID(port_q['tenant_id']))
            proj_obj = self._project_read(proj_id=project_id)
            id_perms = IdPermsType(enable=True)
            port_uuid = str(uuid.uuid4())
            if port_q.get('name'):
                port_name = port_q['name']
            else:
                port_name = port_uuid
            port_obj = VirtualMachineInterface(port_name, proj_obj,
                                               id_perms=id_perms)
            port_obj.uuid = port_uuid
            port_obj.set_virtual_network(net_obj)
            if ('mac_address' in port_q and port_q['mac_address']):
                mac_addrs_obj = MacAddressesType()
                mac_addrs_obj.set_mac_address([port_q['mac_address']])
                port_obj.set_virtual_machine_interface_mac_addresses(mac_addrs_obj)
            port_obj.set_security_group_list([])
            if ('security_groups' not in port_q or
                port_q['security_groups'].__class__ is object):
                sg_obj = SecurityGroup("default", proj_obj)
                port_obj.add_security_group(sg_obj)
        else:  # READ/UPDATE/DELETE
            port_obj = self._virtual_machine_interface_read(
                port_id=port_q['id'], fields=['instance_ip_back_refs',
                                              'floating_ip_back_refs',
                                              'logical_router_back_refs'])

        if 'name' in port_q and port_q['name']:
            port_obj.display_name = port_q['name']

        if port_q.get('device_owner') != constants.DEVICE_OWNER_ROUTER_INTF:
            instance_name = port_q.get('device_id')
            if instance_name:
                try:
                    instance_obj = self._ensure_instance_exists(instance_name)
                    port_obj.set_virtual_machine(instance_obj)
                except RefsExistError as e:
                    exc_info = {'type': 'BadRequest', 'message': str(e)}
                    bottle.abort(400, json.dumps(exc_info))

        if 'security_groups' in port_q:
            port_obj.set_security_group_list([])
            for sg_id in port_q.get('security_groups') or []:
                # TODO optimize to not read sg (only uuid/fqn needed)
                sg_obj = self._vnc_lib.security_group_read(id=sg_id)
                port_obj.add_security_group(sg_obj)

        id_perms = port_obj.get_id_perms()
        if 'admin_state_up' in port_q:
            id_perms.enable = port_q['admin_state_up']
            port_obj.set_id_perms(id_perms)

        if ('extra_dhcp_opts' in port_q):
            dhcp_options = []
            if port_q['extra_dhcp_opts']:
                for option_pair in port_q['extra_dhcp_opts']:
                    option = \
                       DhcpOptionType(dhcp_option_name=option_pair['opt_name'],
                                    dhcp_option_value=option_pair['opt_value'])
                    dhcp_options.append(option)
            if dhcp_options:
                olist = DhcpOptionsListType(dhcp_options)
                port_obj.set_virtual_machine_interface_dhcp_option_list(olist)
            else:
                port_obj.set_virtual_machine_interface_dhcp_option_list(None)

        if ('allowed_address_pairs' in port_q):
            aap_array = []
            if port_q['allowed_address_pairs']:
                for address_pair in port_q['allowed_address_pairs']:
                    mac_refs = \
                        port_obj.get_virtual_machine_interface_mac_addresses()
                    mode = u'active-active';
                    if 'mac_address' not in address_pair:
                        mode = u'active-standby';
                        if mac_refs:
                            address_pair['mac_address'] = mac_refs.mac_address[0]

                    cidr = address_pair['ip_address'].split('/')
                    if len(cidr) == 1:
                        subnet=SubnetType(cidr[0], 32);
                    elif len(cidr) == 2:
                        subnet=SubnetType(cidr[0], int(cidr[1]));
                    else:
                        self._raise_contrail_exception(400,
                               exceptions.BadRequest(resource='port',
                                         msg='Invalid address pair argument'))
                    ip_back_refs = port_obj.get_instance_ip_back_refs()
                    if ip_back_refs:
                        for ip_back_ref in ip_back_refs:
                            iip_uuid = ip_back_ref['uuid']
                            try:
                                ip_obj = self._instance_ip_read(instance_ip_id=\
                                                            ip_back_ref['uuid'])
                            except NoIdError:
                                continue
                            ip_addr = ip_obj.get_instance_ip_address()
                            if ((ip_addr == address_pair['ip_address']) and
                                (mac_refs.mac_address[0] == address_pair['mac_address'])):
                                self._raise_contrail_exception(400,
                                       AddressPairMatchesPortFixedIPAndMac())
                    aap = AllowedAddressPair(subnet,
                                             address_pair['mac_address'], mode)
                    aap_array.append(aap)
            if aap_array:
                aaps = AllowedAddressPairs()
                aaps.set_allowed_address_pair(aap_array)
                port_obj.set_virtual_machine_interface_allowed_address_pairs(aaps)
            else:
                port_obj.set_virtual_machine_interface_allowed_address_pairs(None)

        return port_obj
    #end _port_neutron_to_vnc

    def _port_vnc_to_neutron(self, port_obj, port_req_memo=None):
        port_q_dict = {}
        extra_dict = {}
        extra_dict['contrail:fq_name'] = port_obj.get_fq_name()
        if not port_obj.display_name:
            # for ports created directly via vnc_api
            port_q_dict['name'] = port_obj.get_fq_name()[-1]
        else:
            port_q_dict['name'] = port_obj.display_name
        port_q_dict['id'] = port_obj.uuid

        net_refs = port_obj.get_virtual_network_refs()
        if net_refs:
            net_id = net_refs[0]['uuid']
        else:
            # TODO hack to force network_id on default port
            # as neutron needs it
            net_id = self._vnc_lib.obj_to_id(VirtualNetwork())

        if port_req_memo is None:
            # create a memo only for this port's conversion in this method
            port_req_memo = {}

        if 'networks' not in port_req_memo:
            port_req_memo['networks'] = {}
        if 'subnets' not in port_req_memo:
            port_req_memo['subnets'] = {}

        try:
            net_obj = port_req_memo['networks'][net_id]
        except KeyError:
            net_obj = self._virtual_network_read(net_id=net_id)
            port_req_memo['networks'][net_id] = net_obj
            subnets_info = self._virtual_network_to_subnets(net_obj)
            port_req_memo['subnets'][net_id] = subnets_info

        if port_obj.parent_type != "project":
            proj_id = net_obj.parent_uuid.replace('-', '')
        else:
            proj_id = port_obj.parent_uuid.replace('-', '')
        self._set_obj_tenant_id(port_obj.uuid, proj_id)

        port_q_dict['tenant_id'] = proj_id
        port_q_dict['network_id'] = net_id

        # TODO RHS below may need fixing
        port_q_dict['mac_address'] = ''
        mac_refs = port_obj.get_virtual_machine_interface_mac_addresses()
        if mac_refs:
            port_q_dict['mac_address'] = mac_refs.mac_address[0]

        dhcp_options_list = port_obj.get_virtual_machine_interface_dhcp_option_list()
        if dhcp_options_list and dhcp_options_list.dhcp_option:
            dhcp_options = []
            for dhcp_option in dhcp_options_list.dhcp_option:
                pair = {"opt_value": dhcp_option.dhcp_option_value,
                        "opt_name": dhcp_option.dhcp_option_name}
                dhcp_options.append(pair)
            port_q_dict['extra_dhcp_opts'] = dhcp_options

        allowed_address_pairs = port_obj.get_virtual_machine_interface_allowed_address_pairs()
        if allowed_address_pairs and allowed_address_pairs.allowed_address_pair:
            address_pairs = []
            for aap in allowed_address_pairs.allowed_address_pair:
                pair = {"ip_address": '%s/%s' % (aap.ip.get_ip_prefix(),
                                                 aap.ip.get_ip_prefix_len()),
                        "mac_address": aap.mac}
                address_pairs.append(pair)
            port_q_dict['allowed_address_pairs'] = address_pairs

        port_q_dict['fixed_ips'] = []
        ip_back_refs = port_obj.get_instance_ip_back_refs()
        if ip_back_refs:
            for ip_back_ref in ip_back_refs:
                iip_uuid = ip_back_ref['uuid']
                # fetch it from request context cache/memo if there
                try:
                    ip_obj = port_req_memo['instance-ips'][iip_uuid]
                except KeyError:
                    try:
                        ip_obj = self._instance_ip_read(
                            instance_ip_id=ip_back_ref['uuid'])
                    except NoIdError:
                        continue

                ip_addr = ip_obj.get_instance_ip_address()

                ip_q_dict = {}
                ip_q_dict['port_id'] = port_obj.uuid
                ip_q_dict['ip_address'] = ip_addr
                ip_q_dict['subnet_id'] = self._ip_address_to_subnet_id(ip_addr,
                                                                       net_obj)
                ip_q_dict['net_id'] = net_id

                port_q_dict['fixed_ips'].append(ip_q_dict)

        port_q_dict['security_groups'] = []
        sg_refs = port_obj.get_security_group_refs()
        for sg_ref in sg_refs or []:
            port_q_dict['security_groups'].append(sg_ref['uuid'])

        port_q_dict['admin_state_up'] = port_obj.get_id_perms().enable

        # port can be router interface or vm interface
        # for perf read logical_router_back_ref only when we have to
        port_parent_name = port_obj.parent_name
        router_refs = port_obj.get_logical_router_back_refs()
        if router_refs is not None:
            port_q_dict['device_owner'] = constants.DEVICE_OWNER_ROUTER_INTF
            port_q_dict['device_id'] = router_refs[0]['uuid']
        elif port_obj.parent_type == 'virtual-machine':
            port_q_dict['device_owner'] = ''
            port_q_dict['device_id'] = port_obj.parent_name
        elif port_obj.get_virtual_machine_refs() is not None:
            port_q_dict['device_id'] = \
                port_obj.get_virtual_machine_refs()[0]['to'][-1]
            port_q_dict['device_owner'] = ''
        else:
            port_q_dict['device_id'] = ''
            port_q_dict['device_owner'] = ''

        if port_q_dict['device_id']:
            port_q_dict['status'] = constants.PORT_STATUS_ACTIVE
        else:
            port_q_dict['status'] = constants.PORT_STATUS_DOWN

        if self._contrail_extensions_enabled:
            port_q_dict.update(extra_dict)

        return port_q_dict
    #end _port_vnc_to_neutron

    # public methods
    # network api handlers
    def network_create(self, network_q):
        net_obj = self._network_neutron_to_vnc(network_q, CREATE)
        try:
            net_uuid = self._resource_create('virtual_network', net_obj)
        except RefsExistError:
            self._raise_contrail_exception(400, exceptions.BadRequest(
                resource='network', msg='Network Already exists'))

        if net_obj.router_external:
            fip_pool_obj = FloatingIpPool('floating-ip-pool', net_obj)
            self._floating_ip_pool_create(fip_pool_obj)

        ret_network_q = self._network_vnc_to_neutron(net_obj, net_repr='SHOW')
        self._db_cache['q_networks'][net_uuid] = ret_network_q

        return ret_network_q
    #end network_create

    def network_read(self, net_uuid, fields=None):
        # see if we can return fast...
        #if fields and (len(fields) == 1) and fields[0] == 'tenant_id':
        #    tenant_id = self._get_obj_tenant_id('network', net_uuid)
        #    return {'id': net_uuid, 'tenant_id': tenant_id}

        try:
            net_obj = self._network_read(net_uuid)
        except NoIdError:
            self._raise_contrail_exception(404, exceptions.NetworkNotFound(net_id=net_uuid))

        return self._network_vnc_to_neutron(net_obj, net_repr='SHOW')
    #end network_read

    def network_update(self, net_id, network_q):
        net_obj = self._virtual_network_read(net_id=net_id)
        router_external = net_obj.get_router_external()
        network_q['id'] = net_id
        net_obj = self._network_neutron_to_vnc(network_q, UPDATE)
        if net_obj.router_external and not router_external:
            fip_pools = net_obj.get_floating_ip_pools()
            fip_pool_obj = FloatingIpPool('floating-ip-pool', net_obj)
            self._floating_ip_pool_create(fip_pool_obj)
        if router_external and not net_obj.router_external:
            fip_pools = net_obj.get_floating_ip_pools()
            if fip_pools:
                for fip_pool in fip_pools:
                    try:
                        pool_id = fip_pool['uuid']
                        self._floating_ip_pool_delete(fip_pool_id=pool_id)
                    except RefsExistError:
                        self._raise_contrail_exception(409, exceptions.NetworkInUse(net_id=net_id))
        self._virtual_network_update(net_obj)

        ret_network_q = self._network_vnc_to_neutron(net_obj, net_repr='SHOW')
        self._db_cache['q_networks'][net_id] = ret_network_q

        return ret_network_q
    #end network_update

    def network_delete(self, net_id):
        net_obj = self._virtual_network_read(net_id=net_id)
        self._virtual_network_delete(net_id=net_id)
        try:
            del self._db_cache['q_networks'][net_id]
        except KeyError:
            pass
    #end network_delete

    # TODO request based on filter contents
    def network_list(self, context=None, filters=None):
        ret_dict = {}

        def _collect_without_prune(net_ids):
            for net_id in net_ids:
                try:
                    net_obj = self._network_read(net_id)
                    net_info = self._network_vnc_to_neutron(net_obj,
                                                        net_repr='LIST')
                    ret_dict[net_id] = net_info
                except NoIdError:
                    pass
        #end _collect_without_prune

        # collect phase
        all_net_objs = []  # all n/ws in all projects
        if context and not context['is_admin']:
            if filters and 'id' in filters:
                _collect_without_prune(filters['id'])
            elif filters and 'name' in filters:
                net_objs = self._network_list_project(context['tenant'])
                all_net_objs.extend(net_objs)
                all_net_objs.extend(self._network_list_shared())
                all_net_objs.extend(self._network_list_router_external())
            elif (filters and 'shared' in filters and filters['shared'][0] and
                  'router:external' not in filters):
                all_net_objs.extend(self._network_list_shared())
            elif (filters and 'router:external' in filters and
                  'shared' not in filters):
                all_net_objs.extend(self._network_list_router_external())
            elif (filters and 'router:external' in filters and
                  'shared' in filters):
                all_net_objs.extend(self._network_list_shared_and_ext())
            else:
                project_uuid = str(uuid.UUID(context['tenant']))
                if not filters:
                    all_net_objs.extend(self._network_list_router_external())
                    all_net_objs.extend(self._network_list_shared())
                all_net_objs.extend(self._network_list_project(project_uuid))
        # admin role from here on
        elif filters and 'tenant_id' in filters:
            # project-id is present
            if 'id' in filters:
                # required networks are also specified,
                # just read and populate ret_dict
                # prune is skipped because all_net_objs is empty
                _collect_without_prune(filters['id'])
            else:
                # read all networks in project, and prune below
                proj_ids = [str(uuid.UUID(id)) for id in filters['tenant_id']]
                for p_id in proj_ids:
                    all_net_objs.extend(self._network_list_project(p_id))
                if 'router:external' in filters:
                    all_net_objs.extend(self._network_list_router_external())
        elif filters and 'id' in filters:
            # required networks are specified, just read and populate ret_dict
            # prune is skipped because all_net_objs is empty
            _collect_without_prune(filters['id'])
        elif filters and 'name' in filters:
            net_objs = self._network_list_project(None)
            all_net_objs.extend(net_objs)
        elif filters and 'shared' in filters:
            if filters['shared'][0] == True:
                nets = self._network_list_shared()
                for net in nets:
                    net_info = self._network_vnc_to_neutron(net,
                                                            net_repr='LIST')
                    ret_dict[net.uuid] = net_info
        else:
            # read all networks in all projects
            dom_projects = self._project_list_domain(None)
            project_uuids = [proj['uuid'] for proj in dom_projects]

            for proj_id in project_uuids:
                if not filters or 'router:external' not in filters:
                    all_net_objs.extend(self._network_list_project(proj_id))

            if not filters or 'router:external' in filters:
                all_net_objs.extend(self._network_list_router_external())

        # prune phase
        for net_obj in all_net_objs:
            if net_obj.uuid in ret_dict:
                continue
            net_fq_name = unicode(net_obj.get_fq_name())
            if not self._filters_is_present(filters, 'contrail:fq_name',
                                            net_fq_name):
                continue
            if not self._filters_is_present(filters, 'name',
                                            net_obj.get_display_name()):
                continue
            if net_obj.is_shared == None:
                is_shared = False
            else:
                is_shared = net_obj.is_shared
            if not self._filters_is_present(filters, 'shared',
                                            is_shared):
                continue
            try:
                net_info = self._network_vnc_to_neutron(net_obj,
                                                        net_repr='LIST')
            except NoIdError:
                continue
            ret_dict[net_obj.uuid] = net_info
        ret_list = []
        for net in ret_dict.values():
            ret_list.append(net)

        return ret_list
    #end network_list

    def network_count(self, filters=None):
        nets_info = self.network_list(filters=filters)
        return len(nets_info)
    #end network_count

    # subnet api handlers
    def subnet_create(self, subnet_q):
        net_id = subnet_q['network_id']
        net_obj = self._virtual_network_read(net_id=net_id)

        ipam_fq_name = subnet_q.get('contrail:ipam_fq_name')
        if ipam_fq_name:
            domain_name, project_name, ipam_name = ipam_fq_name

            domain_obj = Domain(domain_name)
            project_obj = Project(project_name, domain_obj)
            netipam_obj = NetworkIpam(ipam_name, project_obj)
        else:  # link with project's default ipam or global default ipam
            try:
                ipam_fq_name = net_obj.get_fq_name()[:-1]
                ipam_fq_name.append('default-network-ipam')
                netipam_obj = self._vnc_lib.network_ipam_read(fq_name=ipam_fq_name)
            except NoIdError:
                netipam_obj = NetworkIpam()
            ipam_fq_name = netipam_obj.get_fq_name()

        subnet_vnc = self._subnet_neutron_to_vnc(subnet_q)
        subnet_key = self._subnet_vnc_get_key(subnet_vnc, net_id)

        # Locate list of subnets to which this subnet has to be appended
        net_ipam_ref = None
        ipam_refs = net_obj.get_network_ipam_refs()
        if ipam_refs:
            for ipam_ref in ipam_refs:
                if ipam_ref['to'] == ipam_fq_name:
                    net_ipam_ref = ipam_ref
                    break

        if not net_ipam_ref:
            # First link from net to this ipam
            vnsn_data = VnSubnetsType([subnet_vnc])
            net_obj.add_network_ipam(netipam_obj, vnsn_data)
        else:  # virtual-network already linked to this ipam
            for subnet in net_ipam_ref['attr'].get_ipam_subnets():
                if subnet_key == self._subnet_vnc_get_key(subnet, net_id):
                    existing_sn_id = self._subnet_vnc_read_mapping(key=subnet_key)
                    # duplicate !!
                    data = {'subnet_cidr': subnet_q['cidr'],
                            'sub_id': existing_sn_id}
                    msg = (_("Cidr %(subnet_cidr)s "
                             "overlaps with another subnet"
                             "of subnet %(sub_id)s") % data)
                    exc_info = {'type': 'BadRequest',
                                'message': msg}
                    bottle.abort(400, json.dumps(exc_info))
                    subnet_info = self._subnet_vnc_to_neutron(subnet,
                                                              net_obj,
                                                              ipam_fq_name)
                    return subnet_info
            vnsn_data = net_ipam_ref['attr']
            vnsn_data.ipam_subnets.append(subnet_vnc)
            # TODO: Add 'ref_update' API that will set this field
            net_obj._pending_field_updates.add('network_ipam_refs')
        self._virtual_network_update(net_obj)

        # allocate an id to the subnet and store mapping with
        # api-server
        subnet_id = str(uuid.uuid4())
        self._subnet_vnc_create_mapping(subnet_id, subnet_key)

        # Read in subnet from server to get updated values for gw etc.
        subnet_vnc = self._subnet_read(net_obj.uuid, subnet_key)
        subnet_info = self._subnet_vnc_to_neutron(subnet_vnc, net_obj,
                                                  ipam_fq_name)

        #self._db_cache['q_subnets'][subnet_id] = subnet_info

        return subnet_info
    #end subnet_create

    def subnet_read(self, subnet_id):
        subnet_key = self._subnet_vnc_read_mapping(id=subnet_id)
        net_id = subnet_key.split()[0]

        try:
            net_obj = self._network_read(net_id)
        except NoIdError:
            self._raise_contrail_exception(404, exceptions.SubnetNotFound(subnet_id=subnet_id))

        ipam_refs = net_obj.get_network_ipam_refs()
        if ipam_refs:
            for ipam_ref in ipam_refs:
                subnet_vncs = ipam_ref['attr'].get_ipam_subnets()
                for subnet_vnc in subnet_vncs:
                    if self._subnet_vnc_get_key(subnet_vnc, net_id) == \
                        subnet_key:
                        ret_subnet_q = self._subnet_vnc_to_neutron(
                            subnet_vnc, net_obj, ipam_ref['to'])
                        self._db_cache['q_subnets'][subnet_id] = ret_subnet_q
                        return ret_subnet_q

        return {}
    #end subnet_read

    def subnet_update(self, subnet_id, subnet_q):
        if 'gateway_ip' in subnet_q:
            if subnet_q['gateway_ip'] != None:
                exc_info = {'type': 'BadRequest',
                            'message': "update of gateway is not supported"}
                bottle.abort(400, json.dumps(exc_info))
 
        if 'allocation_pools' in subnet_q:
            if subnet_q['allocation_pools'] != None:
                exc_info = {'type': 'BadRequest',
                            'message': "update of allocation_pools is not allowed"}
                bottle.abort(400, json.dumps(exc_info))

        subnet_key = self._subnet_vnc_read_mapping(id=subnet_id)
        net_id = subnet_key.split()[0]
        net_obj = self._network_read(net_id)
        ipam_refs = net_obj.get_network_ipam_refs()
        subnet_found = False
        if ipam_refs:
            for ipam_ref in ipam_refs:
                subnets = ipam_ref['attr'].get_ipam_subnets()
                for subnet_vnc in subnets:
                    if self._subnet_vnc_get_key(subnet_vnc,
                               net_id) == subnet_key:
                        subnet_found = True
                        break
                if subnet_found:
                    if 'name' in subnet_q:
                        if subnet_q['name'] != None:
                            subnet_vnc.set_subnet_name(subnet_q['name'])
                    if 'gateway_ip' in subnet_q:
                        if subnet_q['gateway_ip'] != None:
                            subnet_vnc.set_default_gateway(subnet_q['gateway_ip'])

                    if 'enable_dhcp' in subnet_q:
                        if subnet_q['enable_dhcp'] != None:
                            subnet_vnc.set_enable_dhcp(subnet_q['enable_dhcp'])

                    if 'dns_nameservers' in subnet_q:
                        if subnet_q['dns_nameservers'] != None:
                            dhcp_options=[]
                            for dns_server in subnet_q['dns_nameservers']:
                                dhcp_options.append(DhcpOptionType(dhcp_option_name='6',
                                                                   dhcp_option_value=dns_server))
                            if dhcp_options:
                                subnet_vnc.set_dhcp_option_list(DhcpOptionsListType(dhcp_options))
                            else:
                                subnet_vnc.set_dhcp_option_list(None)

                    if 'host_routes' in subnet_q:
                        if subnet_q['host_routes'] != None:
                            host_routes=[]
                            for host_route in subnet_q['host_routes']:
                                host_routes.append(RouteType(prefix=host_route['destination'],
                                                             next_hop=host_route['nexthop']))
                            if host_routes:
                                subnet_vnc.set_host_routes(RouteTableType(host_routes))
                            else:
                                subnet_vnc.set_host_routes(None)

                    net_obj._pending_field_updates.add('network_ipam_refs')
                    self._virtual_network_update(net_obj)
                    ret_subnet_q = self._subnet_vnc_to_neutron(
                                        subnet_vnc, net_obj, ipam_ref['to'])

                    self._db_cache['q_subnets'][subnet_id] = ret_subnet_q
                    return ret_subnet_q

        return {}
    # end subnet_update

    def subnet_delete(self, subnet_id):
        subnet_key = self._subnet_vnc_read_mapping(id=subnet_id)
        net_id = subnet_key.split()[0]

        net_obj = self._network_read(net_id)
        ipam_refs = net_obj.get_network_ipam_refs()
        if ipam_refs:
            for ipam_ref in ipam_refs:
                orig_subnets = ipam_ref['attr'].get_ipam_subnets()
                new_subnets = [subnet_vnc for subnet_vnc in orig_subnets
                               if self._subnet_vnc_get_key(subnet_vnc,
                               net_id) != subnet_key]
                if len(orig_subnets) != len(new_subnets):
                    # matched subnet to be deleted
                    ipam_ref['attr'].set_ipam_subnets(new_subnets)
                    net_obj._pending_field_updates.add('network_ipam_refs')
                    try:
                        self._virtual_network_update(net_obj)
                    except RefsExistError:
                        self._raise_contrail_exception(409, exceptions.SubnetInUse(subnet_id=subnet_id))
                    self._subnet_vnc_delete_mapping(subnet_id, subnet_key)
                    try:
                        del self._db_cache['q_subnets'][subnet_id]
                    except KeyError:
                        pass

                    return
    #end subnet_delete

    def subnets_list(self, context, filters=None):
        ret_subnets = []

        all_net_objs = []
        if filters and 'id' in filters:
            # required subnets are specified,
            # just read in corresponding net_ids
            net_ids = []
            for subnet_id in filters['id']:
                subnet_key = self._subnet_vnc_read_mapping(id=subnet_id)
                net_id = subnet_key.split()[0]
                net_ids.append(net_id)

            all_net_objs.extend(self._virtual_network_list(obj_uuids=net_ids,
                                                           detail=True))
        else:
            if not context['is_admin']:
                proj_id = context['tenant']
            else:
                proj_id = None
            net_objs = self._network_list_project(proj_id)
            all_net_objs.extend(net_objs)
            net_objs = self._network_list_shared()
            all_net_objs.extend(net_objs)

        ret_dict = {}
        for net_obj in all_net_objs:
            if net_obj.uuid in ret_dict:
                continue
            ret_dict[net_obj.uuid] = 1
            ipam_refs = net_obj.get_network_ipam_refs()
            if ipam_refs:
                for ipam_ref in ipam_refs:
                    subnet_vncs = ipam_ref['attr'].get_ipam_subnets()
                    for subnet_vnc in subnet_vncs:
                        sn_info = self._subnet_vnc_to_neutron(subnet_vnc,
                                                              net_obj,
                                                              ipam_ref['to'])
                        sn_id = sn_info['id']
                        sn_proj_id = sn_info['tenant_id']
                        sn_net_id = sn_info['network_id']
                        sn_name = sn_info['name']

                        if (filters and 'shared' in filters and
                                        filters['shared'][0] == True):
                            if not net_obj.is_shared:
                                continue
                        elif filters:
                            if not self._filters_is_present(filters, 'id',
                                                            sn_id):
                                continue
                            if not self._filters_is_present(filters,
                                                            'tenant_id',
                                                            sn_proj_id):
                                continue
                            if not self._filters_is_present(filters,
                                                            'network_id',
                                                            sn_net_id):
                                continue
                            if not self._filters_is_present(filters,
                                                            'name',
                                                            sn_name):
                                continue

                        ret_subnets.append(sn_info)

        return ret_subnets
    #end subnets_list

    def subnets_count(self, context, filters=None):
        subnets_info = self.subnets_list(context, filters)
        return len(subnets_info)
    #end subnets_count

    # ipam api handlers
    def ipam_create(self, ipam_q):
        # TODO remove below once api-server can read and create projects
        # from keystone on startup
        #self._ensure_project_exists(ipam_q['tenant_id'])

        ipam_obj = self._ipam_neutron_to_vnc(ipam_q, CREATE)
        ipam_uuid = self._vnc_lib.network_ipam_create(ipam_obj)

        return self._ipam_vnc_to_neutron(ipam_obj)
    #end ipam_create

    def ipam_read(self, ipam_id):
        try:
            ipam_obj = self._vnc_lib.network_ipam_read(id=ipam_id)
        except NoIdError:
            # TODO add ipam specific exception
            self._raise_contrail_exception(404, exceptions.NetworkNotFound(net_id=ipam_id))

        return self._ipam_vnc_to_neutron(ipam_obj)
    #end ipam_read

    def ipam_update(self, ipam_id, ipam_q):
        ipam_q['id'] = ipam_id
        ipam_obj = self._ipam_neutron_to_vnc(ipam_q, UPDATE)
        self._vnc_lib.network_ipam_update(ipam_obj)

        return self._ipam_vnc_to_neutron(ipam_obj)
    #end ipam_update

    def ipam_delete(self, ipam_id):
        self._vnc_lib.network_ipam_delete(id=ipam_id)
    #end ipam_delete

    # TODO request based on filter contents
    def ipam_list(self, filters=None):
        ret_list = []

        # collect phase
        all_ipams = []  # all ipams in all projects
        if filters and 'tenant_id' in filters:
            project_ids = [str(uuid.UUID(id)) for id in filters['tenant_id']]
            for p_id in project_ids:
                project_ipams = self._ipam_list_project(p_id)
                all_ipams.append(project_ipams)
        else:  # no filters
            dom_projects = self._project_list_domain(None)
            for project in dom_projects:
                proj_id = project['uuid']
                project_ipams = self._ipam_list_project(proj_id)
                all_ipams.append(project_ipams)

        # prune phase
        for project_ipams in all_ipams:
            for proj_ipam in project_ipams:
                # TODO implement same for name specified in filter
                proj_ipam_id = proj_ipam['uuid']
                if not self._filters_is_present(filters, 'id', proj_ipam_id):
                    continue
                ipam_info = self.ipam_read(proj_ipam['uuid'])
                ret_list.append(ipam_info)

        return ret_list
    #end ipam_list

    def ipam_count(self, filters=None):
        ipam_info = self.ipam_list(filters)
        return len(ipam_info)
    #end ipam_count

    # policy api handlers
    def policy_create(self, policy_q):
        # TODO remove below once api-server can read and create projects
        # from keystone on startup
        #self._ensure_project_exists(policy_q['tenant_id'])

        policy_obj = self._policy_neutron_to_vnc(policy_q, CREATE)
        policy_uuid = self._vnc_lib.network_policy_create(policy_obj)

        return self._policy_vnc_to_neutron(policy_obj)
    #end policy_create

    def policy_read(self, policy_id):
        try:
            policy_obj = self._vnc_lib.network_policy_read(id=policy_id)
        except NoIdError:
            raise policy.PolicyNotFound(id=policy_id)

        return self._policy_vnc_to_neutron(policy_obj)
    #end policy_read

    def policy_update(self, policy_id, policy):
        policy_q = policy
        policy_q['id'] = policy_id
        policy_obj = self._policy_neutron_to_vnc(policy_q, UPDATE)
        self._vnc_lib.network_policy_update(policy_obj)

        return self._policy_vnc_to_neutron(policy_obj)
    #end policy_update

    def policy_delete(self, policy_id):
        self._vnc_lib.network_policy_delete(id=policy_id)
    #end policy_delete

    # TODO request based on filter contents
    def policy_list(self, filters=None):
        ret_list = []

        # collect phase
        all_policys = []  # all policys in all projects
        if filters and 'tenant_id' in filters:
            project_ids = [str(uuid.UUID(id)) for id in filters['tenant_id']]
            for p_id in project_ids:
                project_policys = self._policy_list_project(p_id)
                all_policys.append(project_policys)
        else:  # no filters
            dom_projects = self._project_list_domain(None)
            for project in dom_projects:
                proj_id = project['uuid']
                project_policys = self._policy_list_project(proj_id)
                all_policys.append(project_policys)

        # prune phase
        for project_policys in all_policys:
            for proj_policy in project_policys:
                # TODO implement same for name specified in filter
                proj_policy_id = proj_policy['uuid']
                if not self._filters_is_present(filters, 'id', proj_policy_id):
                    continue
                policy_info = self.policy_read(proj_policy['uuid'])
                ret_list.append(policy_info)

        return ret_list
    #end policy_list

    def policy_count(self, filters=None):
        policy_info = self.policy_list(filters)
        return len(policy_info)
    #end policy_count

    def _router_add_gateway(self, router_q, rtr_obj):
        ext_gateway = router_q.get('external_gateway_info', None)
        old_ext_gateway = rtr_obj.get_virtual_network_refs()
        if ext_gateway or old_ext_gateway:
            network_id = ext_gateway.get('network_id', None)
            if network_id:
                if old_ext_gateway and network_id == old_ext_gateway[0]['uuid']:
                    return
                try:
                    net_obj = self._virtual_network_read(net_id=network_id)
                    if not net_obj.get_router_external():
                        exc_info = {'type': 'BadRequest',
                                    'message': "Network %s is not a valid external network" % network_id}
                        bottle.abort(400, json.dumps(exc_info))
                except NoIdError:
                    self._raise_contrail_exception(404, exceptions.NetworkNotFound(net_id=network_id))

                self._router_set_external_gateway(rtr_obj, net_obj)
            else:
                self._router_clear_external_gateway(rtr_obj)

    def _router_set_external_gateway(self, router_obj, ext_net_obj):
        project_obj = self._project_read(proj_id=router_obj.parent_uuid)

        # Get netns SNAT service template
        try:
            st_obj = self._vnc_lib.service_template_read(
                fq_name=SNAT_SERVICE_TEMPLATE_FQ_NAME)
        except NoIdError:
            msg = _("Unable to set or clear the default gateway")
            exc_info = {'type': 'BadRequest', 'message': msg}
            bottle.abort(400, json.dumps(exc_info))

        # Get the service instance if it exists
        si_name = 'si_' + router_obj.uuid
        si_fq_name = project_obj.get_fq_name() + [si_name]
        try:
            si_obj = self._vnc_lib.service_instance_read(fq_name=si_fq_name)
            si_uuid = si_obj.uuid
        except NoIdError:
            si_obj = None

        # Get route table for default route it it exists
        rt_name = 'rt_' + router_obj.uuid
        rt_fq_name = project_obj.get_fq_name() + [rt_name]
        try:
            rt_obj = self._vnc_lib.route_table_read(fq_name=rt_fq_name)
            rt_uuid = rt_obj.uuid
        except NoIdError:
            rt_obj = None

        # Set the service instance
        si_created = False
        if not si_obj:
            si_obj = ServiceInstance(si_name, parent_obj=project_obj)
            si_created = True
        #TODO(ethuleau): For the fail-over SNAT set scale out to 2
        si_prop_obj = ServiceInstanceType(
            right_virtual_network=ext_net_obj.get_fq_name_str(),
            scale_out=ServiceScaleOutType(max_instances=1,
                                          auto_scale=True),
            auto_policy=True)
        si_obj.set_service_instance_properties(si_prop_obj)
        si_obj.set_service_template(st_obj)
        if si_created:
            si_uuid = self._vnc_lib.service_instance_create(si_obj)
        else:
            self._vnc_lib.service_instance_update(si_obj)

        # Set the route table
        route_obj = RouteType(prefix="0.0.0.0/0",
                              next_hop=si_obj.get_fq_name_str())
        rt_created = False
        if not rt_obj:
            rt_obj = RouteTable(name=rt_name, parent_obj=project_obj)
            rt_created = True
        rt_obj.set_routes(RouteTableType.factory([route_obj]))
        if rt_created:
            rt_uuid = self._vnc_lib.route_table_create(rt_obj)
        else:
            self._vnc_lib.route_table_update(rt_obj)

        # Associate route table to all private networks connected onto
        # that router
        for intf in router_obj.get_virtual_machine_interface_refs() or []:
            port_id = intf['uuid']
            net_id = self.port_read(port_id)['network_id']
            try:
                net_obj = self._vnc_lib.virtual_network_read(id=net_id)
            except NoIdError:
                self._raise_contrail_exception(
                    404, exceptions.NetworkNotFound(net_id=net_id))
            net_obj.set_route_table(rt_obj)
            self._vnc_lib.virtual_network_update(net_obj)

        # Add logical gateway virtual network
        router_obj.set_virtual_network(ext_net_obj)
        self._vnc_lib.logical_router_update(router_obj)

    def _router_clear_external_gateway(self, router_obj):
        project_obj = self._project_read(proj_id=router_obj.parent_uuid)

        # Get the service instance if it exists
        si_name = 'si_' + router_obj.uuid
        si_fq_name = project_obj.get_fq_name() + [si_name]
        try:
            si_obj = self._vnc_lib.service_instance_read(fq_name=si_fq_name)
            si_uuid = si_obj.uuid
        except NoIdError:
            si_obj = None

        # Get route table for default route it it exists
        rt_name = 'rt_' + router_obj.uuid
        rt_fq_name = project_obj.get_fq_name() + [rt_name]
        try:
            rt_obj = self._vnc_lib.route_table_read(fq_name=rt_fq_name)
            rt_uuid = rt_obj.uuid
        except NoIdError:
            rt_obj = None

        # Delete route table
        if rt_obj:
            # Disassociate route table to all private networks connected
            # onto that router
            for net_ref in rt_obj.get_virtual_network_back_refs() or []:
                try:
                    net_obj = self._vnc_lib.virtual_network_read(
                        id=net_ref['uuid'])
                except NoIdError:
                    continue
                net_obj.del_route_table(rt_obj)
                self._vnc_lib.virtual_network_update(net_obj)
            self._vnc_lib.route_table_delete(id=rt_obj.uuid)

        # Delete service instance
        if si_obj:
            self._vnc_lib.service_instance_delete(id=si_uuid)

        # Clear logical gateway virtual network
        router_obj.set_virtual_network_list([])
        self._vnc_lib.logical_router_update(router_obj)

    def _set_snat_routing_table(self, router_obj, network_id):
        project_obj = self._project_read(proj_id=router_obj.parent_uuid)
        rt_name = 'rt_' + router_obj.uuid
        rt_fq_name = project_obj.get_fq_name() + [rt_name]

        try:
            rt_obj = self._vnc_lib.route_table_read(fq_name=rt_fq_name)
            rt_uuid = rt_obj.uuid
        except NoIdError:
            # No route table set with that router ID, the gateway is not set
            return

        try:
            net_obj = self._vnc_lib.virtual_network_read(id=network_id)
        except NoIdError:
            raise exceptions.NetworkNotFound(net_id=ext_net_id)
        net_obj.set_route_table(rt_obj)
        self._vnc_lib.virtual_network_update(net_obj)

    def _clear_snat_routing_table(self, router_obj, network_id):
        project_obj = self._project_read(proj_id=router_obj.parent_uuid)
        rt_name = 'rt_' + router_obj.uuid
        rt_fq_name = project_obj.get_fq_name() + [rt_name]

        try:
            rt_obj = self._vnc_lib.route_table_read(fq_name=rt_fq_name)
            rt_uuid = rt_obj.uuid
        except NoIdError:
            # No route table set with that router ID, the gateway is not set
            return

        try:
            net_obj = self._vnc_lib.virtual_network_read(id=network_id)
        except NoIdError:
            raise exceptions.NetworkNotFound(net_id=ext_net_id)
        net_obj.del_route_table(rt_obj)
        self._vnc_lib.virtual_network_update(net_obj)

    # router api handlers
    def router_create(self, router_q):
        #self._ensure_project_exists(router_q['tenant_id'])

        rtr_obj = self._router_neutron_to_vnc(router_q, CREATE)
        rtr_uuid = self._resource_create('logical_router', rtr_obj)
        self._router_add_gateway(router_q, rtr_obj)
        ret_router_q = self._router_vnc_to_neutron(rtr_obj, rtr_repr='SHOW')
        self._db_cache['q_routers'][rtr_uuid] = ret_router_q

        return ret_router_q
    #end router_create

    def router_read(self, rtr_uuid, fields=None):
        # see if we can return fast...
        if fields and (len(fields) == 1) and fields[0] == 'tenant_id':
            tenant_id = self._get_obj_tenant_id('router', rtr_uuid)
            return {'id': rtr_uuid, 'tenant_id': tenant_id}

        try:
            rtr_obj = self._logical_router_read(rtr_uuid)
        except NoIdError:
            self._raise_contrail_exception(404, RouterNotFound(router_id=rtr_uuid))

        return self._router_vnc_to_neutron(rtr_obj, rtr_repr='SHOW')
    #end router_read

    def router_update(self, rtr_id, router_q):
        router_q['id'] = rtr_id
        rtr_obj = self._router_neutron_to_vnc(router_q, UPDATE)
        self._logical_router_update(rtr_obj)
        self._router_add_gateway(router_q, rtr_obj)
        ret_router_q = self._router_vnc_to_neutron(rtr_obj, rtr_repr='SHOW')
        self._db_cache['q_routers'][rtr_id] = ret_router_q

        return ret_router_q
    #end router_update

    def router_delete(self, rtr_id):
        try:
            rtr_obj = self._logical_router_read(rtr_id)
            if rtr_obj.get_virtual_machine_interface_refs():
                self._raise_contrail_exception(409, RouterInUse(router_id=rtr_id))
        except NoIdError:
            self._raise_contrail_exception(404, RouterNotFound(router_id=rtr_id))

        self._router_clear_external_gateway(rtr_obj)
        self._logical_router_delete(rtr_id=rtr_id)
        try:
            del self._db_cache['q_routers'][rtr_id]
        except KeyError:
            pass
    #end router_delete

    # TODO request based on filter contents
    def router_list(self, filters=None):
        ret_list = []

        if filters and 'shared' in filters:
            if filters['shared'][0] == True:
                # no support for shared routers
                return ret_list

        # collect phase
        all_rtrs = []  # all n/ws in all projects
        if filters and 'tenant_id' in filters:
            # project-id is present
            if 'id' in filters:
                # required routers are also specified,
                # just read and populate ret_list
                # prune is skipped because all_rtrs is empty
                for rtr_id in filters['id']:
                    try:
                        rtr_obj = self._logical_router_read(rtr_id)
                        rtr_info = self._router_vnc_to_neutron(rtr_obj,
                                                               rtr_repr='LIST')
                        ret_list.append(rtr_info)
                    except NoIdError:
                        pass
            else:
                # read all routers in project, and prune below
                project_ids = [str(uuid.UUID(id)) \
                               for id in filters['tenant_id']]
                for p_id in project_ids:
                    if 'router:external' in filters:
                        all_rtrs.append(self._fip_pool_ref_routers(p_id))
                    else:
                        project_rtrs = self._router_list_project(p_id)
                        all_rtrs.append(project_rtrs)
        elif filters and 'id' in filters:
            # required routers are specified, just read and populate ret_list
            # prune is skipped because all_rtrs is empty
            for rtr_id in filters['id']:
                try:
                    rtr_obj = self._logical_router_read(rtr_id)
                    rtr_info = self._router_vnc_to_neutron(rtr_obj,
                                                           rtr_repr='LIST')
                    ret_list.append(rtr_info)
                except NoIdError:
                    pass
        else:
            # read all routers in all projects
            dom_projects = self._project_list_domain(None)
            for project in dom_projects:
                proj_id = project['uuid']
                if filters and 'router:external' in filters:
                    all_rtrs.append(self._fip_pool_ref_routers(proj_id))
                else:
                    project_rtrs = self._router_list_project(proj_id)
                    all_rtrs.append(project_rtrs)

        # prune phase
        for project_rtrs in all_rtrs:
            for proj_rtr in project_rtrs:
                proj_rtr_id = proj_rtr['uuid']
                if not self._filters_is_present(filters, 'id', proj_rtr_id):
                    continue

                proj_rtr_fq_name = unicode(proj_rtr['fq_name'])
                if not self._filters_is_present(filters, 'contrail:fq_name',
                                                proj_rtr_fq_name):
                    continue
                try:
                    rtr_obj = self._logical_router_read(proj_rtr['uuid'])
                    rtr_name = rtr_obj.get_display_name()
                    if not self._filters_is_present(filters, 'name', rtr_name):
                        continue
                    rtr_info = self._router_vnc_to_neutron(rtr_obj,
                                                           rtr_repr='LIST')
                except NoIdError:
                    continue
                ret_list.append(rtr_info)

        return ret_list
    #end router_list

    def router_count(self, filters=None):
        rtrs_info = self.router_list(filters)
        return len(rtrs_info)
    #end router_count

    def _check_for_dup_router_subnet(self, router_id,
                                     network_id, subnet_id, subnet_cidr):
        try:
            rports = self.port_list(filters={'device_id': [router_id]})
            # It's possible these ports are on the same network, but
            # different subnets.
            new_ipnet = netaddr.IPNetwork(subnet_cidr)
            for p in rports:
                for ip in p['fixed_ips']:
                    if ip['subnet_id'] == subnet_id:
                       msg = (_("Router %s already has a port "
                                "on subnet %s") % (router_id, subnet_id))
                       self._raise_contrail_exception(400, 
                           exceptions.BadRequest(resource='router', msg=msg))
                    sub_id = ip['subnet_id']
                    subnet = self.subnet_read(sub_id)
                    cidr = subnet['cidr']
                    ipnet = netaddr.IPNetwork(cidr)
                    match1 = netaddr.all_matching_cidrs(new_ipnet, [cidr])
                    match2 = netaddr.all_matching_cidrs(ipnet, [subnet_cidr])
                    if match1 or match2:
                        data = {'subnet_cidr': subnet_cidr,
                                'subnet_id': subnet_id,
                                'cidr': cidr,
                                'sub_id': sub_id}
                        msg = (_("Cidr %(subnet_cidr)s of subnet "
                                 "%(subnet_id)s overlaps with cidr %(cidr)s "
                                 "of subnet %(sub_id)s") % data)
                        exc_info = {'type': 'BadRequest',
                                    'message': msg}
                        bottle.abort(400, json.dumps(exc_info))
        except NoIdError:
            pass

    def add_router_interface(self, router_id, port_id=None, subnet_id=None):
        router_obj = self._logical_router_read(router_id)
        if port_id:
            port = self.port_read(port_id)
            if (port['device_owner'] == constants.DEVICE_OWNER_ROUTER_INTF and
                    port['device_id']):
                self._raise_contrail_exception(409, exceptions.PortInUse(net_id=port['network_id'],
                                           port_id=port['id'],
                                           device_id=port['device_id']))
            fixed_ips = [ip for ip in port['fixed_ips']]
            if len(fixed_ips) != 1:
                msg = _('Router port must have exactly one fixed IP')
                exc_info = {'type': 'BadRequest', 'message': msg}
                bottle.abort(400, json.dumps(exc_info))
            subnet_id = fixed_ips[0]['subnet_id']
            subnet = self.subnet_read(subnet_id)
            self._check_for_dup_router_subnet(router_id,
                                              port['network_id'],
                                              subnet['id'],
                                              subnet['cidr'])

        elif subnet_id:
            subnet = self.subnet_read(subnet_id)
            if not subnet['gateway_ip']:
                msg = _('Subnet for router interface must have a gateway IP')
                exc_info = {'type': 'BadRequest', 'message': msg}
                bottle.abort(400, json.dumps(exc_info))
            self._check_for_dup_router_subnet(router_id,
                                              subnet['network_id'],
                                              subnet_id,
                                              subnet['cidr'])

            fixed_ip = {'ip_address': subnet['gateway_ip'],
                        'subnet_id': subnet['id']}
            port = self.port_create({'tenant_id': subnet['tenant_id'],
                 'network_id': subnet['network_id'],
                 'fixed_ips': [fixed_ip],
                 'admin_state_up': True,
                 'device_id': router_id,
                 'device_owner': constants.DEVICE_OWNER_ROUTER_INTF,
                 'name': ''})

            port_id = port['id']

        else:
            msg = _('Either port or subnet must be specified')
            exc_info = {'type': 'BadRequest', 'message': msg}
            bottle.abort(400, json.dumps(exc_info))

        self._set_snat_routing_table(router_obj, subnet['network_id'])
        vmi_obj = self._vnc_lib.virtual_machine_interface_read(id=port_id)
        router_obj.add_virtual_machine_interface(vmi_obj)
        self._logical_router_update(router_obj)
        info = {'id': router_id,
                'tenant_id': subnet['tenant_id'],
                'port_id': port_id,
                'subnet_id': subnet_id}
        return info
    # end add_router_interface

    def remove_router_interface(self, router_id, port_id=None, subnet_id=None):
        router_obj = self._logical_router_read(router_id)
        subnet = None
        if port_id:
            port_db = self.port_read(port_id)
            if (port_db['device_owner'] != constants.DEVICE_OWNER_ROUTER_INTF
                or port_db['device_id'] != router_id):
                self._raise_contrail_exception(404, RouterInterfaceNotFound(router_id=router_id,
                                                 port_id=port_id))
            port_subnet_id = port_db['fixed_ips'][0]['subnet_id']
            if subnet_id and (port_subnet_id != subnet_id):
                self._raise_contrail_exception(409, exceptions.SubnetMismatchForPort(port_id=port_id,
                                                       subnet_id=subnet_id))
            subnet_id = port_subnet_id
            subnet = self.subnet_read(subnet_id)
            network_id = subnet['network_id']
        elif subnet_id:
            subnet = self.subnet_read(subnet_id)
            network_id = subnet['network_id']

            for intf in router_obj.get_virtual_machine_interface_refs() or []:
                port_id = intf['uuid']
                port_db = self.port_read(port_id)
                if subnet_id == port_db['fixed_ips'][0]['subnet_id']:
                    break
            else:
                msg = _('Subnet %s not connected to router %s') % (subnet_id,
                                                                   router_id)
                exc_info = {'type': 'BadRequest', 'message': msg}
                bottle.abort(400, json.dumps(exc_info))

        self._clear_snat_routing_table(router_obj, subnet['network_id'])
        port_obj = self._virtual_machine_interface_read(port_id)
        router_obj.del_virtual_machine_interface(port_obj)
        self._vnc_lib.logical_router_update(router_obj)
        self.port_delete(port_id)
        info = {'id': router_id,
            'tenant_id': subnet['tenant_id'],
            'port_id': port_id,
            'subnet_id': subnet_id}
        return info
    # end remove_router_interface

    # floatingip api handlers
    def floatingip_create(self, fip_q):
        try:
            fip_obj = self._floatingip_neutron_to_vnc(fip_q, CREATE)
        except Exception, e:
            #logging.exception(e)
            msg = _('Internal error when trying to create floating ip. '
                    'Please be sure the network %s is an external '
                    'network.') % (fip_q['floating_network_id'])
            exc_info = {'type': 'BadRequest', 'message': msg}
            bottle.abort(400, json.dumps(exc_info))
        fip_uuid = self._vnc_lib.floating_ip_create(fip_obj)
        fip_obj = self._vnc_lib.floating_ip_read(id=fip_uuid)

        return self._floatingip_vnc_to_neutron(fip_obj)
    #end floatingip_create

    def floatingip_read(self, fip_uuid):
        try:
            fip_obj = self._vnc_lib.floating_ip_read(id=fip_uuid)
        except NoIdError:
            self._raise_contrail_exception(404, FloatingIPNotFound(floatingip_id=fip_uuid))

        return self._floatingip_vnc_to_neutron(fip_obj)
    #end floatingip_read

    def floatingip_update(self, fip_id, fip_q):
        fip_q['id'] = fip_id
        fip_obj = self._floatingip_neutron_to_vnc(fip_q, UPDATE)
        self._vnc_lib.floating_ip_update(fip_obj)

        return self._floatingip_vnc_to_neutron(fip_obj)
    #end floatingip_update

    def floatingip_delete(self, fip_id):
        self._vnc_lib.floating_ip_delete(id=fip_id)
    #end floatingip_delete

    def floatingip_list(self, context, filters=None):
        # Read in floating ips with either
        # - port(s) as anchor
        # - project(s) as anchor
        # - none as anchor (floating-ip collection)
        ret_list = []

        proj_ids = None
        port_ids = None
        if filters:
            if 'tenant_id' in filters:
                proj_ids = [str(uuid.UUID(id)) for id in filters['tenant_id']]
            elif 'port_id' in filters:
                port_ids = filters['port_id']
        else:  # no filters
            if not context['is_admin']:
                proj_ids = [str(uuid.UUID(context['tenant']))]

        if port_ids:
            fip_objs = self._floatingip_list(back_ref_id=port_ids)
        elif proj_ids:
            fip_objs = self._floatingip_list(back_ref_id=proj_ids)
        else:
            fip_objs = self._floatingip_list()

        for fip_obj in fip_objs:
            if 'floating_ip_address' in filters:
                if (fip_obj.get_floating_ip_address() not in
                        filters['floating_ip_address']):
                    continue
            ret_list.append(self._floatingip_vnc_to_neutron(fip_obj))

        return ret_list
    #end floatingip_list

    def floatingip_count(self, context, filters=None):
        floatingip_info = self.floatingip_list(context, filters)
        return len(floatingip_info)
    #end floatingip_count

    def _ip_addr_in_net_id(self, ip_addr, net_id):
        """Checks if ip address is present in net-id."""
        net_ip_list = [ipobj.get_instance_ip_address() for ipobj in
                                self._instance_ip_list(back_ref_id=[net_id])]
        return ip_addr in net_ip_list

    # port api handlers
    def port_create(self, port_q):
        net_id = port_q['network_id']
        net_obj = self._network_read(net_id)
        proj_id = str(uuid.UUID(port_q['tenant_id']))

        # initialize port object
        port_obj = self._port_neutron_to_vnc(port_q, net_obj, CREATE)

        # if ip address passed then use it
        req_ip_addrs = []
        if 'fixed_ips' in port_q:
            for fixed_ip in port_q['fixed_ips'] or []:
                if 'ip_address' in fixed_ip:
                    ip_addr = fixed_ip['ip_address']
                    # allow duplicate instance-ip objects for ports owned by
                    # routers since it uses gateway ip as address
                    if (port_q['device_owner'] !=
                        constants.DEVICE_OWNER_ROUTER_INTF and
                        self._ip_addr_in_net_id(ip_addr, net_id)):
                           self._raise_contrail_exception(
                               409, exceptions.IpAddressInUse(net_id=net_id,
                               ip_address=ip_addr))
                    req_ip_addrs.append(ip_addr)

        # create the object
        port_id = self._resource_create('virtual_machine_interface', port_obj)

        # initialize ip object
        if net_obj.get_network_ipam_refs():
            def _create_instance_ip(port_obj, ip_addr=None):
                ip_name = str(uuid.uuid4())
                ip_obj = InstanceIp(name=ip_name)
                ip_obj.uuid = ip_name
                ip_obj.set_virtual_machine_interface(port_obj)
                ip_obj.set_virtual_network(net_obj)
                if ip_addr:
                    ip_obj.set_instance_ip_address(ip_addr)

                ip_id = self._instance_ip_create(ip_obj)
                return ip_id

            created_iip_ids = []
            try:
                if req_ip_addrs:
                    for req_ip in req_ip_addrs:
                        created_iip_ids.append(_create_instance_ip(port_obj, req_ip))
                else:
                    _create_instance_ip(port_obj)
            except Exception as e:
                # ResourceExhaustionError, resources are not available
                for iip_id in created_iip_ids:
                    self._instance_ip_delete(instance_ip_id=iip_id)
                self._virtual_machine_interface_delete(port_id=port_id)
                self._raise_contrail_exception(503, exceptions.ResourceExhausted())

        # TODO below reads back default parent name, fix it
        port_obj = self._virtual_machine_interface_read(port_id=port_id,
                                 fields=['instance_ip_back_refs'])

        ret_port_q = self._port_vnc_to_neutron(port_obj)
        #self._db_cache['q_ports'][port_id] = ret_port_q
        self._set_obj_tenant_id(port_id, proj_id)

        # update cache on successful creation
        tenant_id = proj_id.replace('-', '')
        if tenant_id not in self._db_cache['q_tenant_port_count']:
            ncurports = self.port_count({'tenant_id': tenant_id})
        else:
            ncurports = self._db_cache['q_tenant_port_count'][tenant_id]

        self._db_cache['q_tenant_port_count'][tenant_id] = ncurports + 1

        return ret_port_q
    #end port_create

    # TODO add obj param and let caller use below only as a converter
    def port_read(self, port_id):
        try:
            port_obj = self._virtual_machine_interface_read(port_id=port_id)
        except NoIdError:
            self._raise_contrail_exception(404, exceptions.PortNotFound(port_id=port_id))

        ret_port_q = self._port_vnc_to_neutron(port_obj)
        self._db_cache['q_ports'][port_id] = ret_port_q

        return ret_port_q
    #end port_read

    def port_update(self, port_id, port_q):
        # if ip address passed then use it
        req_ip_addrs = []
        req_ip_subnets = []
        port_q['id'] = port_id
        port_obj = self._port_neutron_to_vnc(port_q, None, UPDATE)
        net_id = port_obj.get_virtual_network_refs()[0]['uuid']
        fixed_ips = port_q.get('fixed_ips', [])
        for fixed_ip in fixed_ips:
            if 'ip_address' in fixed_ip:
                ip_addr = fixed_ip['ip_address']
                if self._ip_addr_in_net_id(ip_addr, net_id):
                    self._raise_contrail_exception(409, exceptions.IpAddressInUse(net_id=net_id,
                                                    ip_address=ip_addr))
                req_ip_addrs.append(ip_addr)
            elif 'subnet_id' in fixed_ip:
                req_ip_subnets.append(fixed_ip['subnet_id'])

        self._virtual_machine_interface_update(port_obj)

        if req_ip_addrs or req_ip_subnets:
            net_obj = self._network_read(net_id)
            # initialize ip object
            if net_obj.get_network_ipam_refs():
                def _create_instance_ip(port_obj, ip_addr=None):
                    ip_name = str(uuid.uuid4())
                    ip_obj = InstanceIp(name=ip_name)
                    ip_obj.uuid = ip_name
                    ip_obj.set_virtual_machine_interface(port_obj)
                    ip_obj.set_virtual_network(net_obj)
                    if ip_addr:
                        ip_obj.set_instance_ip_address(ip_addr)

                    ip_id = self._instance_ip_create(ip_obj)
                    return ip_id

                created_iip_ids = []
                try:
                    for subnet_id in req_ip_subnets:
                        subnet_key = self._subnet_vnc_read_mapping(id=subnet_id)
                        ip_addr = self._vnc_lib.virtual_network_ip_alloc(net_obj,
                                                subnet=subnet_key.split()[1])
                        req_ip_addrs.extend(ip_addr)

                    for iip in port_obj.get_instance_ip_back_refs():
                        iip_obj = self._instance_ip_delete(instance_ip_id=iip['uuid'])

                    for new_ip in req_ip_addrs:
                        created_iip_ids.append(_create_instance_ip(port_obj, new_ip))

                except Exception as e:
                    # ResourceExhaustionError, resources are not available
                    for iip_id in created_iip_ids:
                        self._instance_ip_delete(instance_ip_id=iip_id)
                    self._raise_contrail_exception(503, exceptions.ResourceExhausted())

                port_obj = self._virtual_machine_interface_read(port_id=port_id,
                                         fields=['instance_ip_back_refs'])

        ret_port_q = self._port_vnc_to_neutron(port_obj)
        self._db_cache['q_ports'][port_id] = ret_port_q

        return ret_port_q
    #end port_update

    def port_delete(self, port_id):
        port_obj = self._port_neutron_to_vnc({'id': port_id}, None, DELETE)
        if port_obj.parent_type == 'virtual-machine':
            instance_id = port_obj.parent_uuid
        else:
            vm_refs = port_obj.get_virtual_machine_refs()
            if vm_refs:
                instance_id = vm_refs[0]['uuid']
            else:
                instance_id = None
        if port_obj.get_logical_router_back_refs():
            self._raise_contrail_exception(409, L3PortInUse(port_id=port_id,
                device_owner=constants.DEVICE_OWNER_ROUTER_INTF))

        if port_obj.get_logical_router_back_refs():
            self._raise_contrail_exception(409, L3PortInUse(port_id=port_id,
                device_owner=constants.DEVICE_OWNER_ROUTER_INTF))

        # release instance IP address
        iip_back_refs = getattr(port_obj, 'instance_ip_back_refs', None)
        if iip_back_refs:
            for iip_back_ref in iip_back_refs:
                # if name contains IP address then this is shared ip
                iip_obj = self._vnc_lib.instance_ip_read(
                    id=iip_back_ref['uuid'])

                # in case of shared ip only delete the link to the VMI
                if len(iip_obj.name.split(' ')) > 1:
                    iip_obj.del_virtual_machine_interface(port_obj)
                    self._instance_ip_update(iip_obj)
                else:
                    self._instance_ip_delete(
                        instance_ip_id=iip_back_ref['uuid'])

        # disassociate any floating IP used by instance
        fip_back_refs = getattr(port_obj, 'floating_ip_back_refs', None)
        if fip_back_refs:
            for fip_back_ref in fip_back_refs:
                self.floatingip_update(fip_back_ref['uuid'], {'port_id': None})

        tenant_id = self._get_obj_tenant_id('port', port_id)
        self._virtual_machine_interface_delete(port_id=port_id)

        # delete instance if this was the last port
        try:
            if instance_id:
                self._vnc_lib.virtual_machine_delete(id=instance_id)
        except RefsExistError:
            pass
        try:
            del self._db_cache['q_ports'][port_id]
        except KeyError:
            pass

        # update cache on successful deletion
        try:
            self._db_cache['q_tenant_port_count'][tenant_id] -= 1
        except KeyError:
            pass

        self._del_obj_tenant_id(port_id)
    #end port_delete

    def port_list(self, context=None, filters=None):
        project_obj = None
        ret_q_ports = []
        all_project_ids = []

        # TODO used to find dhcp server field. support later...
        if (filters.get('device_owner') == 'network:dhcp' or
            'network:dhcp' in filters.get('device_owner', [])):
             return ret_q_ports

        if not 'device_id' in filters:
            # Listing from back references
            if not filters:
                # TODO once vmi is linked to project in schema, use project_id
                # to limit scope of list
                if not context['is_admin']:
                    project_id = str(uuid.UUID(context['tenant']))
                else:
                    project_id = None

                # read all VMI and IIP in detail one-shot
                if self._list_optimization_enabled:
                    all_port_gevent = gevent.spawn(self._virtual_machine_interface_list,
                                                   parent_id=project_id,
                                                   fields=['instance_ip_back_refs'])
                else:
                    all_port_gevent = gevent.spawn(self._virtual_machine_interface_list,
                                                   fields=['instance_ip_back_refs'])
                port_iip_gevent = gevent.spawn(self._instance_ip_list)
                port_net_gevent = gevent.spawn(self._virtual_network_list,
                                               parent_id=project_id,
                                               detail=True)

                gevent.joinall([all_port_gevent, port_iip_gevent, port_net_gevent])

                all_port_objs = all_port_gevent.value
                port_iip_objs = port_iip_gevent.value
                port_net_objs = port_net_gevent.value

                ret_q_ports = self._port_list(port_net_objs, all_port_objs,
                                              port_iip_objs)

            elif 'tenant_id' in filters:
                all_project_ids = [str(uuid.UUID(id))
                                   for id in filters['tenant_id']]
            elif 'name' in filters:
                all_project_ids = [str(uuid.UUID(context['tenant']))]
            elif 'id' in filters:
                # TODO optimize
                for port_id in filters['id']:
                    try:
                        port_info = self.port_read(port_id)
                    except NoIdError:
                        continue
                    ret_q_ports.append(port_info)

            for proj_id in all_project_ids:
                ret_q_ports = self._port_list_project(proj_id)

            if 'network_id' in filters:
                ret_q_ports = self._port_list_network(filters['network_id'])

            # prune phase
            ret_list = []
            for port_obj in ret_q_ports:
                if not self._filters_is_present(filters, 'name',
                                                port_obj['name']):
                    continue
                ret_list.append(port_obj)
            return ret_list

        # Listing from parent to children
        device_ids = filters['device_id']
        for dev_id in device_ids:
            try:
                # TODO optimize
                port_objs = self._virtual_machine_interface_list(
                                              parent_id=dev_id,
                                              back_ref_id=dev_id,
                                              fields=['instance_ip_back_refs'])
                if not port_objs:
                    raise NoIdError(None)
                for port_obj in port_objs:
                    port_info = self._port_vnc_to_neutron(port_obj)
                    ret_q_ports.append(port_info)
            except NoIdError:
                try:
                    router_obj = self._logical_router_read(rtr_id=dev_id)
                    intfs = router_obj.get_virtual_machine_interface_refs()
                    for intf in (intfs or []):
                        try:
                            port_info = self.port_read(intf['uuid'])
                        except NoIdError:
                            continue
                        ret_q_ports.append(port_info)
                except NoIdError:
                    continue

        return ret_q_ports
    #end port_list

    def port_count(self, filters=None):
        if (filters.get('device_owner') == 'network:dhcp' or
            'network:dhcp' in filters.get('device_owner', [])):
            return 0

        if 'tenant_id' in filters:
            if isinstance(filters['tenant_id'], list):
                project_id = str(uuid.UUID(filters['tenant_id'][0]))
            else:
                project_id = str(uuid.UUID(filters['tenant_id']))

            try:
                nports = self._db_cache['q_tenant_port_count'][project_id]
                if nports < 0:
                    # TBD Hack. fix in case of multiple q servers after 1.03
                    nports = 0
                    del self._db_cache['q_tenant_port_count'][project_id]

                return nports
            except KeyError:
                # do it the hard way but remember for next time
                nports = len(self._port_list_project(project_id))
                self._db_cache['q_tenant_port_count'][project_id] = nports
        else:
            # across all projects - TODO very expensive,
            # get only a count from api-server!
            nports = len(self.port_list(filters=filters))

        return nports
    #end port_count

    # security group api handlers
    def security_group_create(self, sg_q):
        sg_obj = self._security_group_neutron_to_vnc(sg_q, CREATE)
        sg_uuid = self._resource_create('security_group', sg_obj)

        #allow all egress traffic
        def_rule = {}
        def_rule['port_range_min'] = 0
        def_rule['port_range_max'] = 65535
        def_rule['direction'] = 'egress'
        def_rule['remote_ip_prefix'] = '0.0.0.0/0'
        def_rule['remote_group_id'] = None
        def_rule['protocol'] = 'any'
        rule = self._security_group_rule_neutron_to_vnc(def_rule, CREATE)
        self._security_group_rule_create(sg_uuid, rule)

        ret_sg_q = self._security_group_vnc_to_neutron(sg_obj)
        return ret_sg_q
    #end security_group_create

    def security_group_update(self, sg_id, sg_q):
        sg_q['id'] = sg_id
        sg_obj = self._security_group_neutron_to_vnc(sg_q, UPDATE)
        self._vnc_lib.security_group_update(sg_obj)

        ret_sg_q = self._security_group_vnc_to_neutron(sg_obj)

        return ret_sg_q
    #end security_group_update

    def security_group_read(self, sg_id):
        try:
            sg_obj = self._vnc_lib.security_group_read(id=sg_id)
        except NoIdError:
            self._raise_contrail_exception(404, SecurityGroupNotFound(id=sg_id))

        return self._security_group_vnc_to_neutron(sg_obj)
    #end security_group_read

    def security_group_delete(self, sg_id):
        try:
            sg_obj = self._vnc_lib.security_group_read(id=sg_id)
            if sg_obj.name == 'default':
                self._raise_contrail_exception(409, SecurityGroupCannotRemoveDefault())
        except NoIdError:
            return

        try:
            self._security_group_delete(sg_id)
        except RefsExistError:
            self._raise_contrail_exception(409, SecurityGroupInUse(id=sg_id))

        self._db_cache_flush('q_tenant_to_def_sg')
    #end security_group_delete

    def security_group_list(self, context, filters=None):
        ret_list = []

        # collect phase
        all_sgs = []  # all sgs in all projects
        if context and not context['is_admin']:
            for i in range(10):
                project_sgs = self._security_group_list_project(str(uuid.UUID(context['tenant'])))
                if project_sgs:
                    break
                gevent.sleep(3)

            all_sgs.append(project_sgs)
        else: # admin context
            if filters and 'tenant_id' in filters:
                project_ids = [str(uuid.UUID(id)) for id in filters['tenant_id']]
                for p_id in project_ids:
                    project_sgs = self._security_group_list_project(p_id)
                    all_sgs.append(project_sgs)
            else:  # no filters
                all_sgs.append(self._security_group_list_project(None))

        # prune phase
        for project_sgs in all_sgs:
            for sg_obj in project_sgs:
                if not self._filters_is_present(filters, 'id', sg_obj.uuid):
                    continue
                if not self._filters_is_present(filters, 'name',
                                                sg_obj.get_display_name() or sg_obj.name):
                    continue
                sg_info = self._security_group_vnc_to_neutron(sg_obj)
                ret_list.append(sg_info)

        return ret_list
    #end security_group_list

    def _get_ip_proto_number(self, protocol):
        if protocol is None:
            return
        return IP_PROTOCOL_MAP.get(protocol, protocol)

    def _validate_port_range(self, rule):
        """Check that port_range is valid."""
        if (rule['port_range_min'] is None and
            rule['port_range_max'] is None):
            return
        if not rule['protocol']:
            self._raise_contrail_exception(400, SecurityGroupProtocolRequiredWithPorts())
        ip_proto = self._get_ip_proto_number(rule['protocol'])
        if ip_proto in [constants.PROTO_NUM_TCP, constants.PROTO_NUM_UDP]:
            if (rule['port_range_min'] is not None and
                rule['port_range_min'] <= rule['port_range_max']):
                pass
            else:
                self._raise_contrail_exception(400, SecurityGroupInvalidPortRange())
        elif ip_proto == constants.PROTO_NUM_ICMP:
            for attr, field in [('port_range_min', 'type'),
                                ('port_range_max', 'code')]:
                if rule[attr] > 255:
                    self._raise_contrail_exception(400, SecurityGroupInvalidIcmpValue(field=field, attr=attr, value=rule[attr]))
            if (rule['port_range_min'] is None and
                    rule['port_range_max']):
                self._raise_contrail_exception(400, SecurityGroupMissingIcmpType(value=rule['port_range_max']))

    def security_group_rule_create(self, sgr_q):
        self._validate_port_range(sgr_q)
        sg_id = sgr_q['security_group_id']
        sg_rule = self._security_group_rule_neutron_to_vnc(sgr_q, CREATE)
        self._security_group_rule_create(sg_id, sg_rule)
        ret_sg_rule_q = self._security_group_rule_vnc_to_neutron(sg_id,
                                                                 sg_rule)

        return ret_sg_rule_q
    #end security_group_rule_create

    def security_group_rule_read(self, sgr_id):
        sg_obj, sg_rule = self._security_group_rule_find(sgr_id)
        if sg_obj and sg_rule:
            return self._security_group_rule_vnc_to_neutron(sg_obj.uuid,
                                                            sg_rule, sg_obj)

        self._raise_contrail_exception(404, SecurityGroupRuleNotFound(id=sgr_id))
    #end security_group_rule_read

    def security_group_rule_delete(self, sgr_id):
        sg_obj, sg_rule = self._security_group_rule_find(sgr_id)
        if sg_obj and sg_rule:
            return self._security_group_rule_delete(sg_obj, sg_rule)

        self._raise_contrail_exception(404, SecurityGroupRuleNotFound(id=sgr_id))
    #end security_group_rule_delete

    def security_group_rules_read(self, sg_id, sg_obj=None):
        try:
            if not sg_obj:
                sg_obj = self._vnc_lib.security_group_read(id=sg_id)

            sgr_entries = sg_obj.get_security_group_entries()
            sg_rules = []
            if sgr_entries == None:
                return

            for sg_rule in sgr_entries.get_policy_rule():
                sg_info = self._security_group_rule_vnc_to_neutron(sg_obj.uuid,
                                                                   sg_rule,
                                                                   sg_obj)
                sg_rules.append(sg_info)
        except NoIdError:
            self._raise_contrail_exception(404, SecurityGroupNotFound(id=sg_id))

        return sg_rules
    #end security_group_rules_read

    def security_group_rule_list(self, filters=None):
        ret_list = []

        # collect phase
        all_sgs = []
        if filters and 'tenant_id' in filters:
            project_ids = [str(uuid.UUID(id)) for id in filters['tenant_id']]
            for p_id in project_ids:
                project_sgs = self._security_group_list_project(p_id)
                all_sgs.append(project_sgs)
        else:  # no filters
            all_sgs.append(self._security_group_list_project(None))

        # prune phase
        for project_sgs in all_sgs:
            for sg_obj in project_sgs:
                # TODO implement same for name specified in filter
                if not self._filters_is_present(filters, 'id', sg_obj.uuid):
                    continue
                sgr_info = self.security_group_rules_read(sg_obj.uuid, sg_obj)
                if sgr_info:
                    ret_list.extend(sgr_info)

        return ret_list
    #end security_group_rule_list

    #route table api handlers
    def route_table_create(self, rt_q):
        rt_obj = self._route_table_neutron_to_vnc(rt_q, CREATE)
        rt_uuid = self._route_table_create(rt_obj)
        ret_rt_q = self._route_table_vnc_to_neutron(rt_obj)
        return ret_rt_q
    #end security_group_create

    def route_table_read(self, rt_id):
        try:
            rt_obj = self._vnc_lib.route_table_read(id=rt_id)
        except NoIdError:
            # TODO add route table specific exception
            self._raise_contrail_exception(404, exceptions.NetworkNotFound(net_id=rt_id))

        return self._route_table_vnc_to_neutron(rt_obj)
    #end route_table_read

    def route_table_update(self, rt_id, rt_q):
        rt_q['id'] = rt_id
        rt_obj = self._route_table_neutron_to_vnc(rt_q, UPDATE)
        self._vnc_lib.route_table_update(rt_obj)
        return self._route_table_vnc_to_neutron(rt_obj)
    #end policy_update

    def route_table_delete(self, rt_id):
        self._route_table_delete(rt_id)
    #end route_table_delete

    def route_table_list(self, context, filters=None):
        ret_list = []

        # collect phase
        all_rts = []  # all rts in all projects
        if filters and 'tenant_id' in filters:
            project_ids = [str(uuid.UUID(id)) for id in filters['tenant_id']]
            for p_id in project_ids:
                project_rts = self._route_table_list_project(p_id)
                all_rts.append(project_rts)
        elif filters and 'name' in filters:
            p_id = str(uuid.UUID(context['tenant']))
            project_rts = self._route_table_list_project(p_id)
            all_rts.append(project_rts)
        else:  # no filters
            dom_projects = self._project_list_domain(None)
            for project in dom_projects:
                proj_id = project['uuid']
                project_rts = self._route_table_list_project(proj_id)
                all_rts.append(project_rts)

        # prune phase
        for project_rts in all_rts:
            for proj_rt in project_rts:
                # TODO implement same for name specified in filter
                proj_rt_id = proj_rt['uuid']
                if not self._filters_is_present(filters, 'id', proj_rt_id):
                    continue
                rt_info = self.route_table_read(proj_rt_id)
                if not self._filters_is_present(filters, 'name',
                                                rt_info['name']):
                    continue
                ret_list.append(rt_info)

        return ret_list
    #end route_table_list

    #service instance api handlers
    def svc_instance_create(self, si_q):
        si_obj = self._svc_instance_neutron_to_vnc(si_q, CREATE)
        si_uuid = self._svc_instance_create(si_obj)
        ret_si_q = self._svc_instance_vnc_to_neutron(si_obj)
        return ret_si_q
    #end svc_instance_create

    def svc_instance_read(self, si_id):
        try:
            si_obj = self._vnc_lib.service_instance_read(id=si_id)
        except NoIdError:
            # TODO add svc instance specific exception
            self._raise_contrail_exception(404, exceptions.NetworkNotFound(net_id=si_id))

        return self._svc_instance_vnc_to_neutron(si_obj)
    #end svc_instance_read

    def svc_instance_delete(self, si_id):
        self._svc_instance_delete(si_id)
    #end svc_instance_delete

    def svc_instance_list(self, context, filters=None):
        ret_list = []

        # collect phase
        all_sis = []  # all sis in all projects
        if filters and 'tenant_id' in filters:
            project_ids = [str(uuid.UUID(id)) for id in filters['tenant_id']]
            for p_id in project_ids:
                project_sis = self._svc_instance_list_project(p_id)
                all_sis.append(project_sis)
        elif filters and 'name' in filters:
            p_id = str(uuid.UUID(context['tenant']))
            project_sis = self._svc_instance_list_project(p_id)
            all_sis.append(project_sis)
        else:  # no filters
            dom_projects = self._project_list_domain(None)
            for project in dom_projects:
                proj_id = project['uuid']
                project_sis = self._svc_instance_list_project(proj_id)
                all_sis.append(project_sis)

        # prune phase
        for project_sis in all_sis:
            for proj_si in project_sis:
                # TODO implement same for name specified in filter
                proj_si_id = proj_si['uuid']
                if not self._filters_is_present(filters, 'id', proj_si_id):
                    continue
                si_info = self.svc_instance_read(proj_si_id)
                if not self._filters_is_present(filters, 'name',
                                                si_info['name']):
                    continue
                ret_list.append(si_info)

        return ret_list
    #end svc_instance_list

#end class DBInterface
