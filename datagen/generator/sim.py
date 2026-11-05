"""Assembles the simulation from its parts."""
from simulate import SimBase
from flow import FlowMixin
from flow2 import PoMixin
from streams import StreamMixin


class Sim(StreamMixin, PoMixin, FlowMixin, SimBase):
    pass
