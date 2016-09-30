#!/usr/bin/env python3

import argparse
import asyncio
import numpy as np

from .sample_chunker import SampleChunker
from .wlm_data import LSA, MeasurementType
from typing import Iterable
from artiq.protocols.pc_rpc import simple_server_loop
from artiq.tools import verbosity_args, simple_network_args, init_logger


class RPCInterface:
    def __init__(self, lsa: LSA, channels: Iterable[SampleChunker]):
        self._lsa = lsa
        for c in channels:
            self.__setattr__("get_latest_" + c.name, c.get_latest)
            self.__setattr__("get_new_" + c.name, c.get_new)

    def get_latest_spectrum(self):
        """Read the current spectrum an"""
        meas = self._lsa.get_analysis_trace()
        return np.vstack([np.ctypeslib.as_array(x) for x in meas])


def get_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    simple_network_args(parser, 4008)
    verbosity_args(parser)
    return parser


def main() -> None:
    args = get_argparser().parse_args()
    init_logger(args)

    loop = asyncio.get_event_loop()

    channels = dict()

    def reg_chan(name: str, meas_type: MeasurementType) -> None:
        # TODO: Make callback log into Grafana.
        binner = SampleChunker(loop, name, lambda _: print("Finished " + name),
                               256, 30)
        channels[meas_type] = binner

    reg_chan("temperature_celsius", MeasurementType.temperature)
    reg_chan("air_pressure_mbar", MeasurementType.air_pressure)
    reg_chan("wavelength_vac_nm", MeasurementType.wavelength)
    reg_chan("linewidth_vac_nm", MeasurementType.linewidth)
    reg_chan("exposure_1_ms", MeasurementType.exposure_time_1)
    reg_chan("exposure_2_ms", MeasurementType.exposure_time_2)

    def meas_cb(meas_type, meas_value):
        if meas_type in channels:
            channels[meas_type].push(float(meas_value))

    lsa = LSA()

    # Bridge from the driver thread to the main thread.
    lsa.add_callback(lambda *a: loop.call_soon_threadsafe(lambda: meas_cb(*a)))

    rpc_interface = RPCInterface(lsa, channels.values())
    simple_server_loop({"lsa": rpc_interface}, args.bind, args.port)


if __name__ == "__main__":
    main()
