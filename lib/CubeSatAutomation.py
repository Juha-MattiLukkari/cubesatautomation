import socket
import sys
import os
import signal
import subprocess
import thread
import time		
from fcntl import fcntl, F_GETFL, F_SETFL
from os import O_NONBLOCK, read
from ConfigParser import SafeConfigParser
import psutil
import robot
import paramiko

class CubeSatAutomation(object):
	''' Function library for CubeSat test automation
		Version 1.0. Written by Juha-Matti Lukkari 2017-2018.
		Provides low level methods to automate testing of both local terminal based programs
		and remote systems with networking capabilities.
		A local program would be e.g. groundstation software, a remote system could e.g.
		be a Hardware-in-the-loop, such as a satellite subsystem in a testbed.

		Stdin/Stdout of a local program is used for commanding and receiving responses 
		from the program.
		Socket connection is used for commanding and receiving responses from a remote
		program.
		SSH is used to start and close programs for testing in a remote system.
		
	'''

	ROBOT_LIBRARY_SCOPE = 'TEST_SUITE'
	proc = None
	server = None
	port = 0
	sock = None
	ssh = None
	writing = False
	writing_done = False
	reply_buffer = ""

	def __init__(self):
		self.parser = SafeConfigParser()

	def connect_socket(self, server, port, config_file=None, wait_time=2):
		'''	Connect to a network socket 
			Server is either the hostname or the IP address of the host.
			Server and port defined in a config file override the given settings.
		''' 
		if config_file:
			self.parser.read(str(config_file))
			self.server = self.parser.get('SOCKET', 'server')
			self.port = self.parser.get('SOCKET', 'port')

		print "Opening socket connection.."
		CubeSatAutomation.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		server_address = (str(server), int(port))
		CubeSatAutomation.sock.connect(server_address)
		CubeSatAutomation.sock.setblocking(0) 		# For non-blocking network communication
		print "Connected to %s port %s" % server_address
		time.sleep(int(wait_time))

	def close_socket(self):
		''' Close the network socket
		'''
		if CubeSatAutomation.sock:
			print "Closing socket connection"
			CubeSatAutomation.sock.shutdown(socket.SHUT_RDWR)
			CubeSatAutomation.sock.close()
		else:
			print "No socket connection initialized!"

	def program_start(self, prog, params=None, config_file=None, wait_time=5):
		''' Start the program for automation
			Program and params defined in a config file override the given settings. 
		'''
		if config_file:
			self.parser.read(str(config_file))
			prog = self.parser.get('PROGRAM', 'path')
			params = self.parser.get('PROGRAM', 'params')

		print "Opening program for automated control.."
		CubeSatAutomation.proc = subprocess.Popen([str(prog) + " " + str(params)], stdin=subprocess.PIPE, 
			stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True)
		flags = fcntl(CubeSatAutomation.proc.stdout, F_GETFL) 		# get current process stdout flags
		fcntl(CubeSatAutomation.proc.stdout, F_SETFL, flags | O_NONBLOCK)	# For non-blocking stdout communication
		print "Started program " + str(prog) + " with parameters " + str(params)
		time.sleep(int(wait_time))

	def program_close(self):
		''' Close the program we were automating
			Close any existing socket connections as well.
			First tries to close the program in a neat way, if that fails
			then executes the 'kill' command from terminal. 

			Any program that stays alive and doesn't exit after tests have finished
			is a problem for the subsequent tests against the same program.
		'''
		self.close_socket()
		CubeSatAutomation.proc.terminate() 	# Doesn't close the program properly in some cases!

		if os.getpgid(CubeSatAutomation.proc.pid):
			print "Clean termination of the program wasn't successful."
			print "Attempting to terminate from OS.."
			pid = os.getpgid(CubeSatAutomation.proc.pid)
			kill_command = "kill -15 " + "-" + str(pid)
			subprocess.Popen([str(kill_command)], shell=True)	

	def remote_program_start(self, prog, server, port=22, 
						user=None, passw=None, config_file=None, wait_time=5):
		''' Start a program for testing at a remote location through SSH
			Using a config file for setup is preferred.
			Parameters for the program are not defined separatley, but
			should be included to the prog argument.
		'''
		ssh = paramiko.SSHClient()
		ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
		if config_file:
			self.parser.read(str(config_file))
			prog = str(self.parser.get('REMOTE', 'prog'))
			server = str(self.parser.get('REMOTE', 'server'))
			port = int(self.parser.get('REMOTE', 'port'))
			user = str(self.parser.get('REMOTE', 'username'))
			passw = str(self.parser.get('REMOTE', 'password'))

		ssh.connect(hostname=str(server), port=int(port), username=user, password=passw)
		stdin, stdout, stderr = ssh.exec_command(str(prog), get_pty=True)
		print "Started program %s on remote server %s" % (str(prog), str(server))
		CubeSatAutomation.ssh = ssh
		time.sleep(int(wait_time))

	def remote_program_close(self):
		''' Close a remotely started program through SSH
			Simply closes the socket and as get_pty was used, the program should
			terminate on the remote system.
		'''
		CubeSatAutomation.ssh.close()

	def _send_socket(self, message):
		''' Send message through socket connection
		'''
		print "Sending command '%s' through socket connection" % str(message)
		command = str(message) + "\r"
		CubeSatAutomation.sock.sendall(command)

	def _send_console(self, message):
		''' Send message through standard input
		'''
		print "Sending command '%s' through standard input" % str(message)
		command = str(message) + '\r'
		CubeSatAutomation.proc.stdin.write(command)

	def _communicate(self, message):
		''' Choose the communication route for sending commands
		'''
		if CubeSatAutomation.sock:
			self._send_socket(str(message))
		else:
			self._send_console(str(message))

	def _receive(self, timeout, read_timeout):
		''' Choose the communication route for receiving replies
		'''
		if CubeSatAutomation.sock:
			console_lines = self._read_socket(int(timeout), int(read_timeout))
		else:
			console_lines = self._read_console(int(timeout), int(read_timeout))
		return console_lines

	def send_command(self, message, option="Store", timeout=2, read_timeout=2):
		'''	Send commands to the program via the socket connection
			Replies from the socket are read concurrently.
		'''
		self._send_socket(str(message))
		console_lines = self._read_socket(int(timeout), int(read_timeout))
		console_lines = str(console_lines).split("\\n")
		if "Store" in str(option):
			CubeSatAutomation.reply_buffer = console_lines
			print "Console lines"
			print CubeSatAutomation.reply_buffer

	def write_command(self, message, option="Store", timeout=2, read_timeout=2):
		'''	Send commands to the program via standard input
			Replies from standard output are read concurrently.
		'''
		self._send_console(str(message))
		console_lines = self._read_console(int(timeout), int(read_timeout))
		console_lines = str(console_lines).split("\\n")		
		if "Store" in str(option):
			CubeSatAutomation.reply_buffer = console_lines
			print "Console lines"
			print CubeSatAutomation.reply_buffer

	def type_command(self, message, option="Store", timeout=2, read_timeout=2):
		'''	Send commands to the program by simulating typing on a keyboard
			Uses pyautogui library to perform the simulated typing.
			Replies from standard output are read concurrently.

			Use this keyword with caution! When using this keyword, the computer
			shouldn't be used for anything else than performing testing.
		'''
		import pyautogui
		pyautogui.typewrite(str(message))
		pyautogui.press('enter')
		console_lines = self._read_console(int(timeout), int(read_timeout))
		console_lines = str(console_lines).split("\\n")		
		if "Store" in str(option):
			CubeSatAutomation.reply_buffer = console_lines
			print "Console lines"
			print CubeSatAutomation.reply_buffer

	def _read_socket(self, timeout=5, read_timeout=5):
		''' Read messages through the socket
		'''
		print "Reading messages from socket connection"
		socket_lines = []
		time_count = 0
		while time_count < int(timeout):
			time.sleep(1)					# Wait for data to be 'cooked'
			time_count = time_count + 1
			try:
				line = CubeSatAutomation.sock.recv(1024)
			except socket.error:			# No data to be read, wait if more comes
				socket_lines.append("Waiting for more data from socket..\n")
				read_timecount = 0
				while read_timecount < int(read_timeout):
					try:
						line = CubeSatAutomation.sock.recv(1024)
					except socket.error:
						time.sleep(1)
						read_timecount = read_timecount + 1 
						time_count = time_count + 1
						continue
					else:
						break
				if read_timecount >= int(read_timeout): 
					socket_lines.append("Process data read timeout!\n")
					break
			print "sock:" + line.rstrip()
			if line != '':
				socket_lines.append(line)
		return socket_lines

	def _read_console(self, timeout, read_timeout=10):
		'''	Read messages through standard output
		'''
		console_lines = []
		time_count = 0
		while time_count < int(timeout):
			time.sleep(1)					# Wait for data to be 'cooked'
			time_count = time_count + 1
			try:
				line = read(CubeSatAutomation.proc.stdout.fileno(), 1024)
			except OSError:					# No data to be read, wait if more comes
				console_lines.append("Waiting for more data from process..\n")
				read_timecount = 0
				while read_timecount < int(read_timeout):
					try:
						line = read(CubeSatAutomation.proc.stdout.fileno(), 1024)
					except OSError:
						time.sleep(1)
						read_timecount = read_timecount + 1 
						time_count = time_count + 1
						continue
					else:
						break
				if read_timecount >= int(read_timeout): 
					console_lines.append("Process data read timeout!\n")
					break
			print "term:" + line.rstrip()
			if line != '':
				console_lines.append(line)
		return console_lines

	def clear_messages(self, option="Stored", read_timeout=5):
		''' Empty messages that have come through socket connection 
		'''
		if "Stored" in str(option):
			CubeSatAutomation.reply_buffer = ""
		try:
			CubeSatAutomation.sock.recv(1024)
		except socket.error:				# No data to be read, wait if more comes
			read_timecount = 0
			while read_timecount < int(read_timeout):
				try:
					CubeSatAutomation.sock.recv(1024)
				except socket.error:
					time.sleep(1)
					read_timecount = read_timecount + 1 
					continue
				else:
					break	

	def clear_replies(self, option="Stored", read_timeout=5):
		''' Clear process replies
			Flush the stdout and read & discard messages during read_timeout.
			Additionally, empty the reply_buffer
		'''
		CubeSatAutomation.proc.stdout.flush()
		if "Stored" in str(option):
			CubeSatAutomation.reply_buffer = ""
		try:
			read(CubeSatAutomation.proc.stdout.fileno(), 1024)
		except OSError:						# No data to be read, wait if more comes
			read_timecount = 0
			while read_timecount < int(read_timeout):
				try:
					read(CubeSatAutomation.proc.stdout.fileno(), 1024)
				except OSError:
					time.sleep(1)
					read_timecount = read_timecount + 1 
					continue
				else:
					break	
		
	def clear_stored_messages(self):
		'''	Empty the reply_buffer of messages received from the program
		'''
		CubeSatAutomation.reply_buffer = ""

	def _save_program_replies_thread(self, filename, timeout=10, read_timeout=5):
		"""	Write the replies to a file
		"""
		time_count = 0
		while time_count < int(timeout):
			if CubeSatAutomation.writing is False:
				try:	
					f = open(str(filename), 'a')	# Creates a new file if the old one was moved already 
				except IOError:
					raise IOError ("Couldn't open %s" % str(filename)) 	# Joku jarki? Except ja Raise
				console_lines = []
				console_lines = self._receive(int(timeout), int(read_timeout))
				time_count = time_count + int(read_timeout)
				f.writelines(console_lines)
				f.close()
				CubeSatAutomation.writing = True	# Lock the file, implement a proper lock
			else:
				time.sleep(1)
				time_count = time_count + 1
			if CubeSatAutomation.writing_done is True:
				CubeSatAutomation.writing = False
				CubeSatAutomation.writing_done = False
				break

	def save_program_replies(self, filename, timeout=5, read_timeout=20):
		''' Save replies to a file
			Starts a thread that records replies from the system under test
			and saves them to a file.
		'''
		thread.start_new_thread(self._save_program_replies_thread, (filename, timeout, read_timeout))

	def verify_reply_contains(self, message, timeout=5, read_timeout=10):
		''' Read messages from the standard output/socket
			Verify that the specified message is received.
		'''
		console_lines = self._receive(int(timeout), int(read_timeout))
		console_lines = str(console_lines).split("\\n")
		found = False
		for line in console_lines:
			if str(message) in line:
				found = True
				break
		if not found:
			print console_lines				
			raise ValueError ("Message %s was not found in the process replies!\n" % str(message))

	def verify_reply_contains_not(self, message, timeout=5, read_timeout=10):
		''' Read messages from the standard output/socket
			Verify that the specified message isn't received.
		'''
		console_lines = self._receive(int(timeout), int(read_timeout))
		console_lines = str(console_lines).split("\\n")
		found = False
		for line in console_lines:
			if str(message) in line:
				found = True
				break
		if found:
			print console_lines			
			raise ValueError ("Message %s was not supposed to be found in the process replies!\n" % str(message))		

	def verify_reply_contained(self, message):
		'''	Verify if a specified message is contained in the reply_buffer
		'''
		console_lines = str(CubeSatAutomation.reply_buffer).split("\\n")
		found = False
		for line in console_lines:
			if str(message) in line:
				found = True
				break
		if not found:
			print console_lines				
			raise ValueError ("Message %s was not found in the recent process replies!\n" % str(message))

	def verify_reply_contained_not(self, message):
		'''	Verify that a specified message is not contained in the reply_buffer
		'''
		console_lines = str(CubeSatAutomation.reply_buffer).split("\\n")
		found = False
		for line in console_lines:
			if str(message) in line:
				found = True
				break
		if found:
			print console_lines				
			raise ValueError ("Message %s was not supposed to be found in the recent process replies!\n" % str(message))

	def wait_until_reply_contains(self, message, timeout=20, read_timeout=5):
		'''	Wait until a specified message is received
		'''
		completed = False
		found = False
		time_count = 0
		for line in CubeSatAutomation.reply_buffer:
			if str(message) in str(line):
				completed = True
				found = True
		while not completed:
			console_lines = self._receive(1, int(read_timeout))
			console_lines = str(console_lines).split("\\n")
			time_count = time_count + 1
			time.sleep(1)
			if time_count > int(timeout):
				completed = True
			for line in console_lines:
				if str(message) in line:
					found = True
					completed = True
					break
		if not found:
			print console_lines				
			raise ValueError ("Message %s was not found in the process replies!\n" % str(message))

	def verify_saved_reply(self, message, filename, timeout=30):
		''' Check if a desired reply is found from the replies stored to a file
			The file moving stuff is quite odd in this keyword
		'''
		completed = False
		time_count = 0
		while not completed:
			time_count = time_count + 1
			time.sleep(1)
			if time_count > int(timeout):
				print "Time count is larger? " + str(time_count) + " " + str(timeout)
				CubeSatAutomation.writing = False
				CubeSatAutomation.writing_done = True
				completed = True
				break
			if CubeSatAutomation.writing is True:
				try:
					f = open(str(filename), 'r')
				except IOError:
					raise IOError ("Couldn't open %s" % str(filename))
				console_lines = f.readlines()
				for line in console_lines:
					if str(message) in line:
						f.close()
						CubeSatAutomation.writing = False
						CubeSatAutomation.writing_done = True
						completed = True
						break
				f.close()
				CubeSatAutomation.writing = False	# Free the file
				#CubeSatAutomation.writing_done = True
			else:
				continue
		if completed:
			try:
				f = open(str(filename), 'r')
			except IOError:
				print "Verifying couldn't open %s\n" % str(filename)
			console_lines = f.readlines()			
		f.close()
		# Rename and move file
		new_filename = str(os.getcwd()) + "/stored_messages/"  + str(filename) + "_" + str(time.time())	# Unix time
		os.rename(str(filename), new_filename)	
		if time_count > int(timeout):
			raise ValueError("Message %s was not found in the stored process replies!\n" % str(message))	
            
	def persistent_command(self, message, exception_replies, 
						end_reply="None", timeout=5, read_timeout=2):
		''' Sends a command persistently until either time runs out or a certain reply is received
 		'''
		time_count = 0
		completed = False
		found = False
		error_found = False
		command = str(message) + '\r'
		self._communicate(str(message))
		exception_replies = str(exception_replies)
		exception_replies = exception_replies.split(';')
		while not completed:
			if time_count >= int(timeout):
				completed = True
				break        
			console_lines = self._receive(int(timeout), int(read_timeout))
			console_lines = str(console_lines).split("\\n")
			CubeSatAutomation.reply_buffer = console_lines
			for line in console_lines:
				for exception_reply in exception_replies:
					print "exception_reply:" + str(exception_reply)
					print "reply line:" + str(line)
					if str(exception_reply) in str(line):
						print "Exception %s found, retrying to send command" % str(exception_reply)
						self._communicate(str(message))
						break
				# if len(end_reply) > 1:
				if str(end_reply) in str(line):
					completed = True
					found = True
					break
				if "None" in str(end_reply):
					completed = True
					found = True
					break
			time_count = time_count + 1
			time.sleep(1)
		if len(end_reply) > 1:
			if found:
				print "Desired reply %s was found in process replies" % str(end_reply)
			else:
				if str(end_reply) == "Timeout":
					pass
				else:
					raise ValueError ("Desired reply %s was not found in process replies" % str(end_reply))
		if "None" in str(end_reply) or "Timeout" in str(end_reply):
			console_lines = self._receive(1, int(read_timeout))
			console_lines = str(console_lines).split("\\n")
			CubeSatAutomation.reply_buffer = console_lines			
			for line in console_lines:
					print "exception_reply:" + str(exception_reply)
					print "reply line:" + str(line)
					if str(exception_reply) in str(line):
						raise ValueError ("Exception %s still found after timeout" % str(exception_reply)) 				