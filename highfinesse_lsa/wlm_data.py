"""
Interface to the vendor-provided DLL for the HighFinesse LSA laser spectrum
analyser.

<<< Explain WLM, ... terminology. >>>
"""

import logging

from .wlm_data_constants import *
from ctypes import CFUNCTYPE, c_double, c_long, c_ssize_t, POINTER, windll
from enum import Enum, unique
from typing import Callable, Union
import threading


class WlmDataException(Exception):
    """Raised on errors with loading or interfacing to the manufacturer DLL
    (winData.dll)."""
    pass


def _check_control_wlm_error(code: c_long):
    if (code & ~flServerStarted) == 0:
        # No error.
        return code

    # Error messages as per the PDF documentation. The meaning of most of the
    # other constants could probably be inferred from their name.
    messages = {
        flErrDeviceNotFound: "no LSA device found",
        flErrDriverError: "the driver was not loaded correctly or caused the "
                          "device to not start properly",
        flErrUSBError: "a USB error occurred and the device could not be "
                       "started properly",
        flErrUnknownDeviceError: "an unknown device error occurred and the "
                                 "device could not be started properly",
        flErrWrongSN: "the device started has an unexpected serial number",
        flErrUnknownSN: "the device started has an unknown serial number",
        flErrTemperatureError: "an error occurred on initialisation of the "
                               "temperature sensor (wavelength measurements "
                               "will be incorrect)",
        flErrCancelledManually: "device initialisation was cancelled manually"
    }

    msg = ""
    for flag, flag_msg in messages.items():
        if flag & code:
            if msg:
                msg += ", "
            msg += flag_msg
    if not msg:
        msg = "an unknown error occurred"

    msg += " (error code: {:#x}).".format(code)

    raise WlmDataException("Error controlling LSA server application: " + msg)


def _check_get_wlm_version_error(code: c_long):
    if code >= 0:
        return code
    if code == -5:
        raise WlmDataException("Cannot get WLM application version, no server running.")
    raise WlmDataException("Unknown error in GetWLMVersion.")


def _check_set_error(code: c_long):
    """Checks the return value of the Set* family of functions, throwing an
    exception if it indicates an error."""

    if code == ResERR_NoErr:
        return

    messages = {
        ResERR_WlmMissing: "No matching LSA server instance active.",
        ResERR_CouldNotSet: "Value could not be set due to an internal error.",
        ResERR_ParmOutOfRange: "Value to be set exceeds the allowed range.",
        ResERR_WlmOutOfResources: "LSA server out of memory or resources.",
        ResERR_WlmInternalError: "LSA server internal error.",
        ResERR_NotAvailable: "Parameter not available in this LSA version.",
        ResERR_WlmBusy: "The LSA server was busy.",
        ResERR_NotInMeasurementMode: "Function call not allowed in "
                                     "measurement mode.",
        ResERR_OnlyInMeasurementMode: "Function call only allowed in "
                                      "measurement mode.",
        ResERR_ChannelNotAvailable: "Channel index out of range.",
        ResERR_ChannelTemporarilyNotAvailable: "The given channel temporarily isn't"
                                               "available (not in switch mode?).",
        ResERR_CalOptionNotAvailable: "Given calibration option not supported "
                                      "by this LSA.",
        ResERR_CalWavelengthOutOfRange: "Calibration wavelength outside the "
                                        "allowed range.",
        ResERR_BadCalibrationSignal: "Calibration signal is of bad quality "
                                     "(does not match given wavelength?).",
        ResERR_UnitNotAvailable: "Result unit not available."  # It's a mystery.
    }

    msg = messages.get(code, None)
    if not msg:
        msg = "Unknown error occurred (code: {}).".format(code)
    raise WlmDataException(msg)


class Driver:
    """Thin wrapper to annotate the driver DLL with types and return value checking."""
    def __init__(self):
        try:
            lib = windll.wlmData
        except Exception as e:
            raise WlmDataException("Failed to load spectrum analyser DLL: {}".format(e))

        def get_fn(name, result_type, param_types):
            """Look up a function from the DLL handle."""
            fn = getattr(lib, name)
            fn.restype = result_type
            if param_types is not None:
                fn.argtypes = param_types
            return fn

        try:
            # LONG_PTR in winData.h is a pointer-sized signed integer, so
            # equivalent to c_ssize_t. However, there doesn't seem to be a good
            # way of casting a function pointer to an integer in ctypes, so
            # just give up on the parameters.[c_long, c_long, c_ssize_t, c_long]
            self.instantiate = get_fn("Instantiate", c_ssize_t, None)

            self.control_wlm_ex = get_fn("ControlWLMEx", c_long, [c_long, c_long, c_long, c_long, c_long])
            self.control_wlm_ex.restype = lambda x: _check_control_wlm_error(x)

            self.get_wlm_version = get_fn("GetWLMVersion", c_long, [c_long])
            self.get_wlm_version.restype = lambda x: _check_get_wlm_version_error(x)

            self.get_channels_count = get_fn("GetChannelsCount", c_long, [c_long])

            self.get_analysis_item_count = get_fn("GetAnalysisItemCount",
                                                  c_long, [c_long])
            self.get_analysis_item_size = get_fn("GetAnalysisItemSize",
                                                 c_long, [c_long])
            self.get_analysis_data = get_fn("GetAnalysisData",
                                            c_long, [c_long, POINTER(c_double)])
            self.set_analysis = get_fn("SetAnalysis", c_long, [c_long, c_long])
            self.set_analysis.restype = lambda x: _check_set_error(x)
        except Exception as e:
            raise WlmDataException("Error binding to function from DLL: {}".format(e))


@unique
class MeasurementType(Enum):
    #: Reading of the temperature sensor in the device's optics block, in
    #: degrees Celsius.
    temperature = cmiTemperature

    #: Reading of the pressure sensor in the device's optics block, in mbar.
    air_pressure = cmiPressure

    #: Vacuum wavelength, in nm.
    wavelength = cmiWavelength1

    #: Spectral line-width (FWHM) in vacuum, in nanometres.
    linewidth = cmiLinewidth

    #: Exposure time 1, in milliseconds. Updated on manual changes as well as
    #: due to automatic adjustment being active.
    exposure_time_1 = cmiExposureValue1

    #: Exposure time 2, in milliseconds. (Added to exposure_time_1 to determine
    #: the exposure for the fine grating CCD.) Updated on manual changes as well
    #: as due to automatic adjustment being active.
    exposure_time_2 = cmiExposureValue2


_DOUBLE_MEASUREMENT_TYPES = {
    MeasurementType.temperature,
    MeasurementType.air_pressure,
    MeasurementType.wavelength,
    MeasurementType.linewidth
}


def is_double_measurement(meas_type: MeasurementType):
    """The WLM API returns values in two different data types; C doubles and
    longs. Returns whether the passed measurement type is one of those for which
    the double value is to be used."""
    return meas_type in _DOUBLE_MEASUREMENT_TYPES


class LSA:
    _log = logging.getLogger("highfinesse_lsa.LSA")

    def __init__(self, startup_timeout_msecs: int=20000):
        """Initialise a connection to the WLM server application, starting it if
        necessary.

        :param startup_timeout_msecs: The maximum amount of time the constructor
            will block waiting for the server application to start up, in
            milliseconds. An exception will be thrown if the application has not
            been initialised successfully by then. Note that the server takes
            several seconds to initialise the device on startup.
        """
        self._result_callbacks = []
        self._result_callbacks_lock = threading.Lock()

        # We get notified when auto-calibration starts and end, so we can ignore
        # callback events in between.
        self._calibration_active = False

        self._driver = Driver()

        is_running = self._driver.instantiate(c_long(cInstCheckForWLM),
                                              c_long(0), c_ssize_t(0), c_long(0))
        if not is_running:
            self._log.info("HighFinesse WLM application not running; starting via DLL.")

            # TODO: Expose LSA selection ("App" or "Ver" parameter) to user.
            wlm_started = self._driver.control_wlm_ex(cCtrlWLMShow | cCtrlWLMWait,
                                                      0, 0, startup_timeout_msecs, 1)
            if wlm_started == 0:
                raise WlmDataException("Timed out waiting for LSA WLM application to start.")

        wlm_type = self._driver.get_wlm_version(0)
        if wlm_type != 5:
            raise WlmDataException("Expected WLM version type 5 for LSA.")
        self._device_version = self._driver.get_wlm_version(1)
        wlm_revision = self._driver.get_wlm_version(2)
        wlm_compilation = self._driver.get_wlm_version(3)
        self._log.info("Interface to WLM application initialised (ver. %s, rev. %s.%s).",
                       self._device_version, wlm_revision, wlm_compilation)

        if self._driver.get_channels_count(0) != 1:
            raise WlmDataException("More than one LSA channel detected, "
                                   "currently not supported in client code.")

        # The Python ctypes documentation is woefully imprecise here, but it
        # seems logical that we need to keep the callback object itself alive
        # for as long as it is used from C code, not the CFUNCTYPE return value.
        callback_type = CFUNCTYPE(None, c_long, c_long, c_long, c_double, c_long)
        self._c_callback = callback_type(lambda *args: self._callback_ex(*args))
        self._driver.instantiate(c_long(cInstNotification),
                                 c_long(cNotifyInstallCallbackEx),
                                 self._c_callback,
                                 c_long(0))

        # Make analysis data arrays available to this process (this is distinct
        # from the analysis mode).
        self._driver.set_analysis(cSignalAnalysis, 1)

    def __del__(self):
        # We need to make sure to unregister the callback before the associated
        # ctypes object (self._c_callback) is destructed. CPython's documentation
        # on object lifetimes is spectacularly vague, so it isn't clear if
        # self._driver is actually guaranteed to be available still (in which
        # case self._c_callback might already be gone as well, which renders
        # this entire exercise pointless).
        if self._driver:
            self.close()

    def close(self) -> None:
        self._driver.instantiate(c_long(cInstNotification),
                                 c_long(cNotifyRemoveCallback),
                                 c_ssize_t(0), c_long(0))

    def add_callback(self, cb: Callable[[MeasurementType,
                                         Union[int, float]], None]) -> None:
        """Register a callback to be invoked when a new measurement result
        arrives.

        Note that cb will be invoked _from the driver background thread, so it
        needs to be thread-safe.

        This function is thread-safe.
        """
        with self._result_callbacks_lock:
            self._result_callbacks.append(cb)

    def remove_callback(self, cb: Callable[[MeasurementType,
                                            Union[int, float]], None]) -> None:
        """Unregister a previously added measurement result callback.

        This function is thread-safe.
        """
        with self._result_callbacks_lock:
            self._result_callbacks.remove(cb)

    def get_analysis_trace(self):
        """Retrieve the latest analysis "pattern" (trace) from the server
        application.

        :return: A tuple (wavelengths, amplitudes) of two ctypes arrays of
            c_doubles, representing the x- and y-axes of the LSA analysis graph
            (amplitude per wavelength on the CCD).
        """

        length = self._driver.get_analysis_item_count(cSignalAnalysis)
        if length < 0:
            raise WlmDataException("Analysis trace data not ready.")

        elem_size = self._driver.get_analysis_item_size(cSignalAnalysis)
        if elem_size != 8:
            raise WlmDataException("Unexpected data type in analysis data "
                                   "(expected double, got size: {}).".format(elem_size))

        wavelengths = (c_double * length)()
        self._driver.get_analysis_data(cSignalAnalysisX, wavelengths)

        amplitudes = (c_double * length)()
        self._driver.get_analysis_data(cSignalAnalysisY, amplitudes)

        return wavelengths, amplitudes

    def _callback_ex(self, ver: c_long, mode: c_long, intval: c_long,
                     dblval: c_double, res1: c_long) -> None:
        if ver != self._device_version:
            self._log.warn("Ignoring callback invocation for wrong device "
                           "version: %s (should be %s)",
                           ver, self._device_version)
            return

        if mode == cmiStartCalibration:
            self._calibration_active = True
            return

        if mode == cmiEndCalibration:
            self._calibration_active = False
            return

        if self._calibration_active:
            self._log.debug("Calibration active, ignoring update (type %s).", mode)
            return

        if res1 != 0:
            self._log.warn("'res1' callback parameter should always be 0, "
                           "not %s; entered switching mode?", res1)

        try:
            meas_type = MeasurementType(mode)
        except ValueError:
            # Not an event we are interested in.
            return

        # TODO: Handle special values indicating failure (probably just drop
        # those points).
        meas_value = dblval if is_double_measurement(meas_type) else intval
        with self._result_callbacks_lock:
            for cb in self._result_callbacks:
                cb(meas_type, meas_value)
