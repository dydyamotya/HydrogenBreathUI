import logging
import argparse

from ui import app

FORMAT = '%(asctime)s %(message)s'
logger = logging.getLogger(__name__)
file_handler = logging.FileHandler(filename="log.log")
formatter = logging.Formatter(FORMAT)
file_handler.setFormatter(formatter)



def main(debug):
    if debug:
        logging.basicConfig(format=FORMAT, level=logging.DEBUG)
    else:
        logging.basicConfig(format=FORMAT, level=logging.INFO)
    logging.root.addHandler(file_handler)
    app()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true")

    args = parser.parse_args()
    main(args.debug)

# See PyCharm help at https://www.jetbrains.com/help/pycharm/
