from . import wlm_data

if __name__ == "__main__":
    import logging
    logging.basicConfig()
    logging.getLogger().setLevel(logging.DEBUG)
    l = wlm_data.LSA()
    l.add_callback(lambda ty, val: print("{}: {}".format(ty, val)))
    while True:
        import time
        time.sleep(1)
