#!/usr/bin/env python3
import sys
import os
import logging
from contextlib import closing
from argparse import ArgumentParser
from json import loads, load
from msgbus.client import MsgbusSubClient
import pyircbot
import traceback
from pyircbot.pyircbot import PrimitiveBot
from pyircbot.irccore import IRCEvent, UserPrefix, IRCCore
from pyircbot.common import TouchReload
from json import dumps


class PyIRCBotSub(PrimitiveBot):
    def __init__(self, name, host, port, config):
        super().__init__(config)
        self.name = name
        self.host = host
        self.port = port
        self.meta = {}
        self.client = None  # PubSub socket

    def run(self):
        # Connect to msgbus and loop through messages
        with closing(MsgbusSubClient(self.host, self.port)) as self.client:
            self.client.prepare_pub()
            self.client.sub("pyircbot_privmsg")
            self.client.sub("pyircbot_join")
            self.client.sub("pyircbot_kick")
            self.client.sub("pyircbot_part")
            self.client.sub("pyircbot_mode")
            self.client.sub("pyircbot_quit")
            self.client.sub("pyircbot_meta_update")
            self.client.pub("pyircbot_meta_req", "x")
            while True:
                try:
                    channel, body = self.client.recv()
                    self.process_line(channel, body)
                except Exception as e:
                    traceback.print_exc()

    def process_line(self, channel, body):
        name, rest = body.split(" ", 1)
        if name != self.name:
            return

        command = channel.split("_", 1)[1]

        if command == "meta_update":
            self.meta.update(loads(rest))
            print(self.meta)
            return

        args, sender, trailing, extras = loads(rest)
        nick, username, hostname = extras["prefix"]

        msg = IRCCore.packetAsObject(command.upper(),
                                     args,
                                     f"{nick}!{username}@{hostname}",   # hack
                                     trailing)

        for module_name, module in self.moduleInstances.items():
            for hook in module.irchooks:
                validation = hook.validator(msg, self)
                if validation:
                    hook.method(msg, validation)

    " Filesystem Methods "
    def getConfigPath(self, moduleName):
        """Return the absolute path for a module's config file

        :param moduleName: the module who's config file we want
        :type moduleName: str"""

        basepath = "%s/config/%s" % (self.botconfig["bot"]["datadir"], moduleName)

        if os.path.exists("%s.json" % basepath):
            return "%s.json" % basepath
        return None

    def getDataPath(self, moduleName):
        """Return the absolute path for a module's data dir

        :param moduleName: the module who's data dir we want
        :type moduleName: str"""
        module_dir = os.path.join(self.botconfig["bot"]["datadir"], "data", moduleName)
        if not os.path.exists(module_dir):
            os.mkdir(module_dir)
        return module_dir

    def act_PRIVMSG(self, towho, message):
        """Use the `/msg` command

        :param towho: the target #channel or user's name
        :type towho: str
        :param message: the message to send
        :type message: str"""
        # self.sendRaw("PRIVMSG %s :%s" % (towho, message))
        self.client.pub("pyircbot_send", "{} {} {}".format(self.name, "privmsg", dumps([towho, message])))

    def getBestModuleForService(self, service):
        if service == "services":
            return self
        return super().getBestModuleForService(service)

    def nick(self):
        return self.meta.get("nick", None)


def main():
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)-15s %(levelname)-8s %(filename)s:%(lineno)d %(message)s")
    log = logging.getLogger('main')

    # parse command line args
    parser = ArgumentParser(description="Run pyircbot plugins behind a pubsub client")
    parser.add_argument("-c", "--config", help="Pyircbot config file")
    parser.add_argument("-s", "--server", default="localhost", help="Msgbus server address")
    parser.add_argument("-p", "--port", default=7100, type=int, help="Msgbus server port")
    parser.add_argument("-n", "--name", default="default", help="bot name")
    parser.add_argument("--debug", action="store_true", help="increase logging level")
    parser.add_argument("--touch-reload", action="store_true", help="reload modules on file modification")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    log.debug(args)

    # Load config
    with open(args.config) as f:
        config = load(f)

    bot = PyIRCBotSub(args.name, args.server, int(args.port), config)

    # Load modules in config
    moddir = os.path.join(os.path.dirname(pyircbot.__file__), "modules")
    modpaths = []
    sys.path.append(moddir)
    for modulename in config["modules"]:
        bot.loadmodule(modulename)
        modpaths.append(os.path.join(moddir, modulename + ".py"))

    if args.touch_reload:
        def changed(path):
            module_name = os.path.basename(path).split(".")[-2]
            logging.warning("{} was modified, reloading".format(module_name))
            if module_name in bot.moduleInstances.keys():  # TODO fix shitty mystery results from redomodule in core
                bot.redomodule(module_name)
            else:
                bot.loadmodule(module_name)

        reloader = TouchReload(modpaths, changed)
        reloader.daemon = True
        reloader.start()

    bot.run()


if __name__ == "__main__":
    main()
