#!/usr/bin/env python3

import sys, os, tempfile, functools
from datetime import datetime
from argparse import ArgumentParser

import commands
from utils import merge_keys_to_list
from cache import Cache
from local import local_run
from version import VERSION

def compare_output(old, new, commandlist=None):
	anomalies = []
	if not old:
		# if None is passed in it's simply empty
		old = {}
	tasks = merge_keys_to_list(old, new)
	for task_name in tasks:
		cmd = commands.COMMAND_CACHE.get(task_name, None)
		if not cmd:
			anomalies.append(commands.W("unknown command with name %s found (cache generated by older version?)" % (task_name)))
			continue
		if commandlist and task_name not in commandlist:
			continue
	
		old_data = old[task_name] if task_name in old else ""
		old_data = cmd.parse(old_data)
		new_data = cmd.parse(new[task_name])
		ret = cmd.compare(old_data, new_data)
		if type(ret) != list:
			raise Exception("unexpected return value type for %s" % cmd)
		if ret and len(ret) > 0:
			anomalies = anomalies + ret
	return anomalies

def print_anomalies(anomalies, show_debug=False, show_color=True):
	changes = list(filter(lambda x:x[0] == commands.CHANGE, anomalies))
	warning = list(filter(lambda x:x[0] == commands.WARNING, anomalies))
	debug = list(filter(lambda x:x[0] == commands.DEBUG, anomalies)) if show_debug else []
	c1 = "\x1b[32m" if show_color else ""
	c2 = "\x1b[31m" if show_color else ""
	c3 = "\x1b[36m" if show_color else ""
	c4 = "\x1b[34m" if show_color else ""
	c_end = "\x1b[0m" if show_color else ""
	la, lw, ld = len(changes), len(warning), len(debug)
	debugs = " and %i debug message%s" % (ld, "s" if ld != 1 else "") if ld > 0 else ""
	print("%s%i change%s detected (%i warning%s%s)%s" % (c1, la, "s" if la != 1 else "", lw, "s" if lw != 1 else "", debugs, c_end))
	for w in warning:
		print("%s! %s%s" % (c2, w[1], c_end))
	for c in changes:
		print("%s+ %s%s" % (c3, c[1], c_end))
	if show_debug:
		for d in debug:
			print("%s- %s%s" % (c4, d[1], c_end))

def run(tmpdirname):

	default_max_cache_entries = 16
	default_cache_name = ".dawgmon.db"

	# parsing and checking arguments
	parser = ArgumentParser(description="attack surface analyzer and change monitor")

	group = parser.add_mutually_exclusive_group()
	group.add_argument("-A", help="analyze system (default)", dest="analyze", action="store_true", default=False)
	group.add_argument("-C", help="compare cache entry id1 with id2", dest="compare_cache", metavar=("id1", "id2"), nargs=2, type=int)
	group.add_argument("-E", help="list available commands", dest="list_commands", action="store_true", default=False)
	group.add_argument("-L", help="list cache entries", dest="list_cache", action="store_true", default=False)

	parser.add_argument("-d", help="show debug output", dest="show_debug", action="store_true", default=False)
	parser.add_argument("-e", help="execute specific command", dest="commandlist", metavar="command", type=str, action="append")
	parser.add_argument("-f", help="force action even if not seteuid root", dest="force", default=False, action="store_true")
	parser.add_argument("-g", help="colorize the analysis output", dest="colorize", default=False, action="store_true")
	parser.add_argument("-l", help="location of database cache (default: $HOME/%s)" % (default_cache_name), dest="cache_location", metavar="filename", default=None, required=False)
	parser.add_argument("-m", help="max amount of cache entries per host (default: %i)" % default_max_cache_entries,
		dest="max_cache_entries", type=int, metavar="N", default=default_max_cache_entries, required=False)
	parser.add_argument("-v", "--version", action="version", version="dawgmon %s" % VERSION)
	args = parser.parse_args()

	if args.max_cache_entries < 1 or args.max_cache_entries > 1024:
		print("maximum number of cache entries invalid or set too high [1-1024]")
		sys.exit(1)

	if not args.cache_location:
		args.cache_location = os.path.join(os.getenv("HOME"), default_cache_name)

	if not args.list_cache and not args.list_commands and not args.analyze and not args.compare_cache:
		print("select an action -A/C/E/L")
		return

	if not args.force and os.geteuid() != 0 and args.analyze:
		print("It's strongly recommended to run an analysis as root.")
		answer = input("Continue anyway with the analysis y/n? ")
		if len(answer) != 1 or answer[0].lower() != 'y':
			return

	# load last entry from cache
	cache = Cache(args.cache_location)
	cache.load()

	# list all the entries available in the cache
	if args.list_cache:
		entries = cache.get_entries()
		print("  ID\tTIMESTAMP")
		for entry in entries:
			print("{:4d}\t%s".format(entry["id"]) % (entry["timestamp"]))
		return
	# list all the commands available
	elif args.list_commands:
		cmd_list = list(commands.COMMAND_CACHE.keys())
		cmd_list.sort()
		for cmd in cmd_list:
			print(cmd)
		return

	# only add results to cache if a full analysis was run
	add_to_cache = not args.commandlist and args.analyze

	# if no commandlist specified add all available commands
	if not args.commandlist:
		args.commandlist = []	
		for cmd in commands.COMMANDS:
				args.commandlist.append(cmd.name)


	# run the selected list of commands or get cached results
	anomalies = []
	if args.analyze:
		new = local_run(tmpdirname, args.commandlist)
		# add new entry to cache if needed but only if a full command list is being executed
		old = cache.get_last_entry()
		if add_to_cache:
			if not old:
				anomalies.append(commands.W("no cache entry found yet so caching baseline"))
			cache.add_entry(new)
		else:
			anomalies.append(commands.W("results NOT cached as only partial command list being run"))
	else:
		new = cache.get_entry(args.compare_cache[1])
		if not new:
			print("cannot find cache entry with id %i" % args.compare_cache[1])
			return
		old = cache.get_entry(args.compare_cache[0])
		if not old:
			print("cannot find cache entry with id %i" % args.compare_cache[0])
			return

	# merge the list of differences with the previous list this is done
	# such that the warnings added above will appear first when outputting
	# the warnings later on in print_anomalies
	anomalies = anomalies + compare_output(old, new, args.commandlist)

	# output the detected anomalies
	print_anomalies(anomalies, args.show_debug, args.colorize)

	# update the cache
	cache.purge(args.max_cache_entries)
	cache.save()

def main():
	with tempfile.TemporaryDirectory() as tmpdirname:
		run(tmpdirname)

if __name__ == "__main__":
	main()
