""" btu/but_api/scheduler.py """

from enum import Enum
import time

import json
import pathlib
import socket
import frappe

# https://realpython.com/python-sockets/#application-protocol-header

# pylint: disable=invalid-name
class RequestType(Enum):
	create_task_schedule = 0
	ping = 1
	cancel_task_schedule = 2

class SchedulerAPI():
	"""
	Static methods are for external use.
	"""

	@frappe.whitelist()
	@staticmethod
	def send_ping():
		"""
		Ask the BTU Scheduler to reply with a 'pong'
		"""
		response = SchedulerAPI().send_message(RequestType.ping, content=None)
		return response

	@frappe.whitelist()
	@staticmethod
	def reload_task_schedule(task_schedule_id):
		"""
		Ask the BTU Scheduler to reload the Task Schedule in RQ, using the latest information.
		NOTE: This does not perform an immediate Task execution; it only refreshes the JQ Job and CRON schedule.
		"""
		response = SchedulerAPI().send_message(RequestType.create_task_schedule,
		                                       content=task_schedule_id)
		return response

	@frappe.whitelist()
	@staticmethod
	def cancel_task_schedule(task_schedule_id):
		"""
		Ask the BTU Scheduler to cancel the Task Schedule in RQ.
		"""
		response = SchedulerAPI().send_message(RequestType.cancel_task_schedule,
		                                       content=task_schedule_id)
		return response


	def send_message(self, request_type: RequestType, content):

		if not isinstance(request_type, RequestType):
			raise Exception("Argument 'request_type' must be an enum of RequestType.")
		new_message = {
			'request_type': request_type.name,
			'request_content': content
		}
		message_as_string = json.dumps(new_message)
		return self._send_message_to_scheduler_socket(message_as_string)

	def _send_message_to_scheduler_socket(self, message, debug=True):
		"""
		Establish a connection to the BTU scheduler daemon's Unix Domain Socket, and send a message.
		"""
		if not isinstance(message, str):
			raise TypeError("Argument 'message' must be a UTF-8 string.")

		socket_str = frappe.db.get_single_value("BTU Configuration", "path_to_btu_scheduler_uds")
		if not socket_str:
			raise ValueError("BTU Configuration is missing a path to the Unix Domain Socket for the scheduler daemon.")

		# Create a UDS socket; connect to the port where the BTU Scheduler daemon is listening.
		socket_path = pathlib.Path(socket_str)
		if not socket_path.exists():
			raise FileNotFoundError(f"Path to socket file does not exists: '{socket_path.absolute()}'")

		try:
			scheduler_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
			scheduler_socket.settimeout(5)  # Very important, otherwise indefinite wait time.
			scheduler_socket.connect(str(socket_path.absolute()))
			if debug:
				print(f"Connected to BTU Scheduler daemon via Unix Domain Socket at '{socket_path}'")
				print(f"Blocking: {scheduler_socket.getblocking()}")
				print(f"Timeout: {scheduler_socket.gettimeout()}")
		except Exception as ex:
			return f"Exception while connecting to BTU Scheduler socket: {str(ex)}"

		message_bytes = message.encode('utf-8')
		uds_response = None
		try:
			bytes_sent = scheduler_socket.send(message_bytes)
			if debug:
				print(f"Transmitted this quantity of bytes to UDS server: {bytes_sent}")
			time.sleep(0.5)  # brief wait for server to reply
			uds_response = scheduler_socket.recv(2048)  # response should be much smaller than 2kb
			if debug:
				print(f"Byte response from BTU Scheduler: {uds_response}")
		except Exception as ex:
			print(f"Exception while communicating with the BTU Scheduler daemon's Unix Domain Socket: {ex}")
		finally:
			scheduler_socket.close()
			if debug:
				print("Socket connection to BTU Scheduler daemon is now closed.")

		if uds_response:
			uds_response = uds_response.decode('utf-8')  # return UTF-8 string
		return uds_response

'''
import sys
import selectors
import json
import io
import struct

class Message:
    def __init__(self, selector, sock, addr, request):
        self.selector = selector
        self.sock = sock
        self.addr = addr
        self.request = request
        self._recv_buffer = b""
        self._send_buffer = b""
        self._request_queued = False
        self._jsonheader_len = None
        self.jsonheader = None
        self.response = None

    def _set_selector_events_mask(self, mode):
        """Set selector to listen for events: mode is 'r', 'w', or 'rw'."""
        if mode == "r":
            events = selectors.EVENT_READ
        elif mode == "w":
            events = selectors.EVENT_WRITE
        elif mode == "rw":
            events = selectors.EVENT_READ | selectors.EVENT_WRITE
        else:
            raise ValueError(f"Invalid events mask mode {repr(mode)}.")
        self.selector.modify(self.sock, events, data=self)

    def _read(self):
        try:
            # Should be ready to read
            data = self.sock.recv(4096)
        except BlockingIOError:
            # Resource temporarily unavailable (errno EWOULDBLOCK)
            pass
        else:
            if data:
                self._recv_buffer += data
            else:
                raise RuntimeError("Peer closed.")

    def _write(self):
        if self._send_buffer:
            print("sending", repr(self._send_buffer), "to", self.addr)
            try:
                # Should be ready to write
                sent = self.sock.send(self._send_buffer)
            except BlockingIOError:
                # Resource temporarily unavailable (errno EWOULDBLOCK)
                pass
            else:
                self._send_buffer = self._send_buffer[sent:]

    def _json_encode(self, obj, encoding):
        return json.dumps(obj, ensure_ascii=False).encode(encoding)

    def _json_decode(self, json_bytes, encoding):
        tiow = io.TextIOWrapper(
            io.BytesIO(json_bytes), encoding=encoding, newline=""
        )
        obj = json.load(tiow)
        tiow.close()
        return obj

    def _create_message(
        self, *, content_bytes, content_type, content_encoding
    ):
        jsonheader = {
            "byteorder": sys.byteorder,
            "content-type": content_type,
            "content-encoding": content_encoding,
            "content-length": len(content_bytes),
        }
        jsonheader_bytes = self._json_encode(jsonheader, "utf-8")
        message_hdr = struct.pack(">H", len(jsonheader_bytes))
        message = message_hdr + jsonheader_bytes + content_bytes
        return message

    def _process_response_json_content(self):
        content = self.response
        result = content.get("result")
        print(f"got result: {result}")

    def _process_response_binary_content(self):
        content = self.response
        print(f"got response: {repr(content)}")

    def process_events(self, mask):
        if mask & selectors.EVENT_READ:
            self.read()
        if mask & selectors.EVENT_WRITE:
            self.write()

    def read(self):
        self._read()

        if self._jsonheader_len is None:
            self.process_protoheader()

        if self._jsonheader_len is not None:
            if self.jsonheader is None:
                self.process_jsonheader()

        if self.jsonheader:
            if self.response is None:
                self.process_response()

    def write(self):
        if not self._request_queued:
            self.queue_request()

        self._write()

        if self._request_queued:
            if not self._send_buffer:
                # Set selector to listen for read events, we're done writing.
                self._set_selector_events_mask("r")

    def close(self):
        print("closing connection to", self.addr)
        try:
            self.selector.unregister(self.sock)
        except Exception as e:
            print(
                "error: selector.unregister() exception for",
                f"{self.addr}: {repr(e)}",
            )

        try:
            self.sock.close()
        except OSError as e:
            print(
                "error: socket.close() exception for",
                f"{self.addr}: {repr(e)}",
            )
        finally:
            # Delete reference to socket object for garbage collection
            self.sock = None

    def queue_request(self):
        content = self.request["content"]
        content_type = self.request["type"]
        content_encoding = self.request["encoding"]
        if content_type == "text/json":
            req = {
                "content_bytes": self._json_encode(content, content_encoding),
                "content_type": content_type,
                "content_encoding": content_encoding,
            }
        else:
            req = {
                "content_bytes": content,
                "content_type": content_type,
                "content_encoding": content_encoding,
            }
        message = self._create_message(**req)
        self._send_buffer += message
        self._request_queued = True

    def process_protoheader(self):
        hdrlen = 2
        if len(self._recv_buffer) >= hdrlen:
            self._jsonheader_len = struct.unpack(
                ">H", self._recv_buffer[:hdrlen]
            )[0]
            self._recv_buffer = self._recv_buffer[hdrlen:]

    def process_jsonheader(self):
        hdrlen = self._jsonheader_len
        if len(self._recv_buffer) >= hdrlen:
            self.jsonheader = self._json_decode(
                self._recv_buffer[:hdrlen], "utf-8"
            )
            self._recv_buffer = self._recv_buffer[hdrlen:]
            for reqhdr in (
                "byteorder",
                "content-length",
                "content-type",
                "content-encoding",
            ):
                if reqhdr not in self.jsonheader:
                    raise ValueError(f'Missing required header "{reqhdr}".')

    def process_response(self):
        content_len = self.jsonheader["content-length"]
        if not len(self._recv_buffer) >= content_len:
            return
        data = self._recv_buffer[:content_len]
        self._recv_buffer = self._recv_buffer[content_len:]
        if self.jsonheader["content-type"] == "text/json":
            encoding = self.jsonheader["content-encoding"]
            self.response = self._json_decode(data, encoding)
            print("received response", repr(self.response), "from", self.addr)
            self._process_response_json_content()
        else:
            # Binary or unknown content-type
            self.response = data
            print(
                f'received {self.jsonheader["content-type"]} response from',
                self.addr,
            )
            self._process_response_binary_content()
        # Close when response has been processed
        self.close()
'''
