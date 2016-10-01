#!/usr/bin/env python3

import aiohttp
import argparse
import asyncio
import atexit
import logging
import numpy as np
import time

from .sample_chunker import SampleChunker
from .wlm_data import LSA, MeasurementType
from typing import Iterable
from artiq.protocols.pc_rpc import Server
from artiq.tools import atexit_register_coroutine, bind_address_from_args, init_logger, simple_network_args, TaskObject, verbosity_args

logger = logging.getLogger(__name__)


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


def get_argparser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--influxdb-endpoint", default=None,
                        help="InfluxDB write endpoint to push data to (e,g. "
                        "http://localhost:8086/write?db=mydb)")
    parser.add_argument("--influxdb-tags", default="system=pulsar,device=lsa")
    simple_network_args(parser, 4008)
    verbosity_args(parser)
    return parser


class InfluxDBExporter(TaskObject):
    def __init__(self, loop, write_endpoint, tags):
        self._loop = loop
        self.write_endpoint = write_endpoint
        self.tags = tags

        self._queue = asyncio.Queue(100)

    def push(self, field, values):
        data = np.array(values)
        stats = {
            "min": np.min(data),
            "p20": np.percentile(data, 20),
            "mean": np.mean(data),
            "p80": np.percentile(data, 80),
            "max": np.max(data)
        }

        try:
            self._queue.put_nowait((field, stats, time.time()))
        except asyncio.QueueFull:
            logger.warning("failed to update dataset '%s': "
                           "too many pending updates", field)

    async def _do(self):
        while True:
            field, stats, timestamp = await self._queue.get()

            values = ",".join(["{}={}".format(k, v) for k, v in stats.items()])
            body = "{},{} {} {}".format(
                field, self.tags, values, round(timestamp * 1e3))

            async with aiohttp.ClientSession(loop=self._loop) as client:
                async with client.post(self.write_endpoint + "&precision=ms",
                                       data=body) as resp:
                    if resp.status != 204:
                        logger.warning("got HTTP status %d trying to "
                                       "update '%s': %s", resp.status, field,
                                       (await resp.text()).strip())


def main() -> None:
    args = get_argparser().parse_args()
    init_logger(args)

    loop = asyncio.get_event_loop()
    atexit.register(loop.close)

    exporter = None
    if args.influxdb_endpoint:
        exporter = InfluxDBExporter(loop, args.influxdb_endpoint, args.influxdb_tags)
        exporter.start()
        atexit_register_coroutine(exporter.stop)

    channels = dict()

    def reg_chan(name: str, meas_type: MeasurementType) -> None:
        def cb(values):
            if exporter:
                exporter.push(name, values)
        chunker = SampleChunker(loop, name, cb, 256, 30)
        channels[meas_type] = chunker

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
    rpc_server = Server({"lsa": rpc_interface}, builtin_terminate=True)
    loop.run_until_complete(rpc_server.start(bind_address_from_args(args), args.port))
    atexit_register_coroutine(rpc_server.stop)

    loop.run_until_complete(rpc_server.wait_terminate())


if __name__ == "__main__":
    main()
