#
# Copyright (c) 2014 Juniper Networks, Inc. All rights reserved.
#

"""
This file contains implementation of data model for SVC monitor
"""
from vnc_api.common.exceptions import NoIdError
from pysandesh.gen_py.sandesh.ttypes import SandeshLevel
from cfgm_common.vnc_db import DBBase

class LoadbalancerPoolSM(DBBase):
    _dict = {}
    obj_type = 'loadbalancer_pool'

    def __init__(self, uuid, obj_dict=None):
        self.uuid = uuid
        self.members = set()
        self.loadbalancer_healthmonitors = set()
        self.service_instance = None
        self.virtual_machine_interface = None
        self.virtual_ip = None
        self.update(obj_dict)
        self.last_sent = None
    # end __init__

    def update(self, obj=None):
        obj = self.read_obj(self.uuid)
        self.name = obj['fq_name'][-1]
        self.params = obj['loadbalancer_pool_properties']
        self.provider = obj['loadbalancer_pool_provider']
        self.members = set([lm['uuid'] for lm in obj.get('loadbalancer_members', [])])
        self.id_perms = obj['id_perms']
        self.parent_uuid = obj['parent_uuid']
        self.display_name = obj['display_name']
        self.update_single_ref('service_instance', obj)
        self.update_single_ref('virtual_ip', obj)
        self.update_single_ref('virtual_machine_interface', obj)
        self.update_multiple_refs('loadbalancer_healthmonitor', obj)
    # end update

    @classmethod
    def delete(cls, uuid):
        if uuid not in cls._dict:
            return
        obj = cls._dict[uuid]
        cls._manager.loadbalancer_agent.delete_loadbalancer_pool(obj)
        obj.update_single_ref('service_instance', {})
        obj.update_single_ref('virtual_ip', {})
        obj.update_single_ref('virtual_machine_interface', {})
        obj.update_multiple_refs('loadbalancer_healthmonitor', {})
        del cls._dict[uuid]
    # end delete

    def add(self):
        self.last_sent = self._manager.loadbalancer_agent.loadbalancer_pool_add(self)
        if len(self.members):
            for member in self.members:
                member_obj = LoadbalancerMemberSM.get(member)
                member_obj.last_sent = self._manager.loadbalancer_agent.loadbalancer_member_add(member_obj)
        if self.virtual_ip:
            vip_obj = VirtualIpSM.get(self.virtual_ip)
            vip_obj.last_sent = self._manager.loadbalancer_agent.virtual_ip_add(vip_obj)
    # end add
# end class LoadbalancerPoolSM

class LoadbalancerMemberSM(DBBase):
    _dict = {}
    obj_type = 'loadbalancer_member'

    def __init__(self, uuid, obj_dict=None):
        self.uuid = uuid
        self.loadbalancer_pool = {}
        self.update(obj_dict)
        self.last_sent = None
        if self.loadbalancer_pool:
            parent = LoadbalancerPoolSM.get(self.loadbalancer_pool)
            parent.members.add(self.uuid)
    # end __init__

    def update(self, obj=None):
        obj = self.read_obj(self.uuid)
        self.name = obj['fq_name'][-1]
        self.params = obj['loadbalancer_member_properties']
        self.loadbalancer_pool = self.get_parent_uuid(obj)
        self.id_perms = obj['id_perms']
    # end update

    @classmethod
    def delete(cls, uuid):
        if uuid not in cls._dict:
            return
        obj = cls._dict[uuid]
        cls._manager.loadbalancer_agent.delete_loadbalancer_member(obj)
        if obj.loadbalancer_pool:
            parent = LoadbalancerPoolSM.get(obj.loadbalancer_pool)
        if parent:
            parent.members.discard(obj.uuid)
        del cls._dict[uuid]
    # end delete
# end class LoadbalancerMemberSM

class VirtualIpSM(DBBase):
    _dict = {}
    obj_type = 'virtual_ip'

    def __init__(self, uuid, obj_dict=None):
        self.uuid = uuid
        self.virtual_machine_interface = None
        self.loadbalancer_pool = None
        self.update(obj_dict)
        self.last_sent = None
    # end __init__

    def update(self, obj=None):
        obj = self.read_obj(self.uuid)
        self.name = obj['fq_name'][-1]
        self.params = obj['virtual_ip_properties']
        self.update_single_ref('virtual_machine_interface', obj)
        self.update_single_ref('loadbalancer_pool', obj)
        self.id_perms = obj['id_perms']
        self.parent_uuid = obj['parent_uuid']
        self.display_name = obj['display_name']
    # end update

    @classmethod
    def delete(cls, uuid):
        if uuid not in cls._dict:
            return
        obj = cls._dict[uuid]
        cls._manager.loadbalancer_agent.delete_virtual_ip(obj)
        obj.update_single_ref('virtual_machine_interface', {})
        obj.update_single_ref('loadbalancer_pool', {})
        del cls._dict[uuid]
    # end delete

# end class VirtualIpSM

class HealthMonitorSM(DBBase):
    _dict = {}
    obj_type = 'loadbalancer_healthmonitor'

    def __init__(self, uuid, obj_dict=None):
        self.uuid = uuid
        self.loadbalancer_pools = set()
        self.last_sent = None
        self.update(obj_dict)
    # end __init__

    def update(self, obj=None):
        obj = self.read_obj(self.uuid)
        self.name = obj['fq_name'][-1]
        self.params = obj['loadbalancer_healthmonitor_properties']
        self.update_multiple_refs('loadbalancer_pool', obj)
        self.id_perms = obj['id_perms']
        self.parent_uuid = obj['parent_uuid']
        self.display_name = obj['display_name']
        self.last_sent = self._manager.loadbalancer_agent.update_hm(self)
    # end update

    @classmethod
    def delete(cls, uuid):
        if uuid not in cls._dict:
            return
        obj = cls._dict[uuid]
        obj.update_multiple_refs('loadbalancer_pool', {})
        del cls._dict[uuid]
    # end delete
# end class HealthMonitorSM


class VirtualMachineInterfaceSM(DBBase):
    _dict = {}
    obj_type = 'virtual_machine_interface'

    def __init__(self, uuid, obj_dict=None):
        self.uuid = uuid
        self.virtual_ip = None
        self.virtual_network = None
        self.loadbalancer_pool = None
        self.logical_interface = None
        self.instance_ip = None
        self.update(obj_dict)
    # end __init__

    def update(self, obj=None):
        obj = self.read_obj(self.uuid)
        self.name = obj['fq_name'][-1]
        self.update_single_ref('virtual_ip', obj)
        self.update_single_ref('loadbalancer_pool', obj)
        self.update_single_ref('instance_ip', obj)
        self.update_single_ref('virtual_network', obj)
        self.update_single_ref('logical_interface', obj)
    # end update

    @classmethod
    def delete(cls, uuid):
        if uuid not in cls._dict:
            return
        obj = cls._dict[uuid]
        obj.update_single_ref('virtual_ip', {})
        obj.update_single_ref('loadbalancer_pool', {})
        obj.update_single_ref('instance_ip', {})
        obj.update_single_ref('virtual_network', {})
        self.update_single_ref('logical_interface', {})
        del cls._dict[uuid]
    # end delete
# end VirtualMachineInterfaceSM

class ServiceInstanceSM(DBBase):
    _dict = {}
    obj_type = 'service_instance'

    def __init__(self, uuid, obj_dict=None):
        self.uuid = uuid
        self.service_template = None
        self.loadbalancer_pool = None
        self.update(obj_dict)
    # end __init__

    def update(self, obj=None):
        obj = self.read_obj(self.uuid)
        self.name = obj['fq_name'][-1]
        self.params = obj['service_instance_properties']
        self.update_single_ref('service_template', obj)
        self.update_single_ref('loadbalancer_pool', obj)
        self.id_perms = obj['id_perms']
    # end update

    @classmethod
    def delete(cls, uuid):
        if uuid not in cls._dict:
            return
        obj = cls._dict[uuid]
        obj.update_single_ref('service_template', {})
        obj.update_single_ref('loadbalancer_pool', {})
        del cls._dict[uuid]
    # end delete
# end class ServiceInstanceSM


class ServiceTemplateSM(DBBase):
    _dict = {}
    obj_type = 'service_template'

    def __init__(self, uuid, obj_dict=None):
        self.uuid = uuid
        self.service_instances = set()
        self.update(obj_dict)
    # end __init__

    def update(self, obj=None):
        obj = self.read_obj(self.uuid)
        self.name = obj['fq_name'][-1]
        self.params = obj['service_template_properties']
        self.update_multiple_refs('service_instance', obj)
        self.id_perms = obj['id_perms']
    # end update

    @classmethod
    def delete(cls, uuid):
        if uuid not in cls._dict:
            return
        obj = cls._dict[uuid]
        obj.update_multiple_refs('service_instance', {})
        del cls._dict[uuid]
    # end delete
# end class ServiceInstanceSM


class VirtualNetworkSM(DBBase):
    _dict = {}
    obj_type = 'virtual_network'

    def __init__(self, uuid, obj_dict=None):
        self.uuid = uuid
        self.virtual_machine_interfaces = set()
        self.update(obj_dict)
    # end __init__

    def update(self, obj=None):
        obj = self.read_obj(self.uuid)
        self.name = obj['fq_name'][-1]
        self.fq_name = obj['fq_name']
        self.update_multiple_refs('virtual_machine_interface', obj)
    # end update

    @classmethod
    def delete(cls, uuid):
        if uuid not in cls._dict:
            return
        obj = cls._dict[uuid]
        obj.update_multiple_refs('virtual_machine_interface', {})
        del cls._dict[uuid]
    # end delete

# end class VirtualNetworkSM


class InstanceIpSM(DBBase):
    _dict = {}
    obj_type = 'instance_ip'

    def __init__(self, uuid, obj_dict=None):
        self.uuid = uuid
        self.address = None
        self.virtual_machine_interface = None
        self.update(obj_dict)
    # end __init__

    def update(self, obj=None):
        obj = self.read_obj(self.uuid)
        self.name = obj['fq_name'][-1]
        self.address = obj['instance_ip_address']
        self.update_single_ref('virtual_machine_interface', obj)
    # end update

    @classmethod
    def delete(cls, uuid):
        if uuid not in cls._dict:
            return
        obj = cls._dict[uuid]
        obj.update_single_ref('instance_ip', {})
        del cls._dict[uuid]
    # end delete
# end class InstanceIpSM

class LogicalInterfaceSM(DBBase):
    _dict = {}
    obj_type = 'logical_interface'

    def __init__(self, uuid, obj_dict=None):
        self.uuid = uuid
        self.virtual_machine_interface = None
        self.logical_interface_vlan_tag = 0
        self.update(obj_dict)
        if self.physical_interface:
            parent = PhysicalInterfaceSM.get(self.physical_interface)
        elif self.physical_router:
            parent = PhysicalRouterSM.get(self.physical_router)
        if parent:
            parent.logical_interfaces.add(self.uuid)
    # end __init__

    def update(self, obj=None):
        obj = self.read_obj(self.uuid)
        if obj['parent_type'] == 'physical-router':
            self.physical_router = self.get_parent_uuid(obj)
            self.physical_interface = None
        else:
            self.physical_interface = self.get_parent_uuid(obj)
            self.physical_router = None

        self.update_single_ref('virtual_machine_interface', obj)
        self.name = obj['fq_name'][-1]
        self.logical_interface_vlan_tag = obj.get('logical_interface_vlan_tag', 0)
    # end update

    @classmethod
    def delete(cls, uuid):
        if uuid not in cls._dict:
            return
        obj = cls._dict[uuid]
        if obj.physical_interface:
            parent = PhysicalInterfaceSM.get(obj.physical_interface)
        elif obj.physical_router:
            parent = PhysicalInterfaceSM.get(obj.physical_router)
        if parent:
            parent.logical_interfaces.discard(obj.uuid)
        obj.update_single_ref('virtual_machine_interface', {})
        del cls._dict[uuid]
    # end delete
# end LogicalInterfaceSM

class PhysicalInterfaceSM(DBBase):
    _dict = {}
    obj_type = 'physical_interface'

    def __init__(self, uuid, obj_dict=None):
        self.uuid = uuid
        self.update(obj_dict)
        pr = PhysicalRouterSM.get(self.physical_router)
        if pr:
            pr.physical_interfaces.add(self.uuid)
    # end __init__

    def update(self, obj=None):
        obj = self.read_obj(self.uuid)
        self.physical_router = self.get_parent_uuid(obj)
        self.logical_interfaces = set([li['uuid'] for li in
                                       obj.get('logical_interfaces', [])])
    # end update

    @classmethod
    def delete(cls, uuid):
        if uuid not in cls._dict:
            return
        obj = cls._dict[uuid]
        pr = PhysicalRouterSM.get(obj.physical_router)
        if pr:
            pr.physical_interfaces.discard(obj.uuid)
        del cls._dict[uuid]
    # end delete
# end PhysicalInterfaceSM

class PhysicalRouterSM(DBBase):
    _dict = {}
    obj_type = 'physical_router'

    def __init__(self, uuid, obj_dict=None):
        self.uuid = uuid
        self.update(obj_dict)
    # end __init__

    def update(self, obj=None):
        obj = self.read_obj(self.uuid)
        self.management_ip = obj.get('physical_router_management_ip')
        self.vendor = obj.get('physical_router_vendor_name')
        self.physical_interfaces = set([pi['uuid'] for pi in
                                        obj.get('physical_interfaces', [])])
        self.logical_interfaces = set([li['uuid'] for li in
                                       obj.get('logical_interfaces', [])])
    # end update

    @classmethod
    def delete(cls, uuid):
        if uuid not in cls._dict:
            return
        obj = cls._dict[uuid]
        del cls._dict[uuid]
    # end delete
# end PhysicalRouterSM


class ProjectSM(DBBase):
    _dict = {}
    obj_type = 'project'

    def __init__(self, uuid, obj_dict=None):
        self.uuid = uuid
        self.update(obj_dict)
    # end __init__

    def update(self, obj=None):
        obj = self.read_obj(self.uuid)
        self.name = obj['fq_name'][-1]
        self.fq_name = obj['fq_name']
    # end update

    @classmethod
    def delete(cls, uuid):
        if uuid not in cls._dict:
            return
        obj = cls._dict[uuid]
        del cls._dict[uuid]
    # end delete
# end ProjectSM


DBBase._OBJ_TYPE_MAP = {
    'loadbalancer_pool': LoadbalancerPoolSM,
    'loadbalancer_member': LoadbalancerMemberSM,
    'virtual_ip': VirtualIpSM,
    'loadbalancer_healthmonitor': HealthMonitorSM,
    'service_template': ServiceTemplateSM,
    'service_instance': ServiceInstanceSM,
    'virtual_network': VirtualNetworkSM,
    'virtual_machine_interface': VirtualMachineInterfaceSM,
    'instance_ip': InstanceIpSM,
    'logical_interface': LogicalInterfaceSM,
    'physical_interface': PhysicalInterfaceSM,
    'physical_router': PhysicalRouterSM,
    'project': ProjectSM,
}
