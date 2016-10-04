#!/usr/bin/env python3

import atexit
import numpy as np

from llama.influxdb import aggregate_stats_default
from llama.rpc import add_chunker_methods, run_simple_rpc_server
from llama.sample_chunker import SampleChunker
from .wlm_data import LSA, MeasurementType


class RPCInterface:
    def __init__(self, lsa, channels):
        self._lsa = lsa
        for c in channels:
            add_chunker_methods(self, c)

    def get_latest_spectrum(self):
        """Read the current spectrum and return"""
        meas = self._lsa.get_analysis_trace()
        return np.vstack([np.ctypeslib.as_array(x) for x in meas]).T.copy()


def setup_interface(args, influx_pusher, loop):
    channels = dict()

    def reg_chan(name: str, meas_type: MeasurementType) -> None:
        def cb(values):
            if influx_pusher:
                influx_pusher.push(name, aggregate_stats_default(values))
        chunker = SampleChunker(name, cb, 256, 30, loop)
        channels[meas_type] = chunker

    reg_chan("temperature_celsius", MeasurementType.temperature)
    reg_chan("air_pressure_mbar", MeasurementType.air_pressure)
    reg_chan("wavelength_vac_nm", MeasurementType.wavelength)
    reg_chan("linewidth_vac_nm", MeasurementType.linewidth)
    reg_chan("exposure_1_ms", MeasurementType.exposure_time_1)
    reg_chan("exposure_2_ms", MeasurementType.exposure_time_2)

    lsa = LSA()
    atexit.register(lsa.close)

    # Bridge from the driver thread to the main thread.
    def meas_cb(meas_type, meas_value):
        if meas_type in channels:
            channels[meas_type].push(float(meas_value))
    lsa.add_callback(lambda *a: loop.call_soon_threadsafe(lambda: meas_cb(*a)))

    return RPCInterface(lsa, channels.values())


if __name__ == "__main__":
    run_simple_rpc_server(4008, None, "lsa", setup_interface)
