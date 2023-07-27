import os
import time
import logging as lg
from datetime import datetime

import aiohttp
import uvicorn
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from enum import Enum
import requests
import asyncio
import random
import threading


lg.basicConfig(level=lg.INFO)

app = FastAPI()

NODES = ('node-1', 'node-2', 'node-3')


LOW_TIMEOUT = 2500
HIGH_TIMEOUT = 5000

REQUESTS_TIMEOUT = 1000
HB_TIME = 1000
MAX_LOG_WAIT = 1000


class Node:
    def __init__(self, own_host, others_host):
        self.role = 'FOLLOWER'
        self.term = 0
        self.commit_idx = 0
        self.own_host = own_host
        self.others_host = others_host
        self.majority = ((len(self.others_host) + 1) // 2) + 1
        self.election_timeout = None
        self.timeout_thread: threading.Thread | None = None
        self.staged = {}
        self.initial()

    @staticmethod
    def random_timeout():
        return random.randrange(LOW_TIMEOUT, HIGH_TIMEOUT) / 1000

    def initial(self):
        self.set_election_timeout()

        if self.timeout_thread and self.timeout_thread.isAlive():
            return

        self.timeout_thread = threading.Thread(target=self.timeout_loop)
        self.timeout_thread.start()

    def set_election_timeout(self):
        self.election_timeout = time.time() + self.random_timeout()

    def timeout_loop(self):
        # цей метод робить таймаут перед тим, як розпочати вибори лідера.

        while self.role != 'LEADER':
            delta = self.election_timeout - time.time()
            if delta < 0:
                self.start_election()
            else:
                time.sleep(delta)

    def start_election(self):
        pass

    def vote_decision(self, term, commit_idx, staged):

        if self.term < term and self.commit_idx <= commit_idx and (staged or (self.staged == staged)):
            self.set_election_timeout()
            self.term = term
            return True, self.term
        else:
            return False, self.term


current_node: Node | None = None


class HeartbeatResponse(BaseModel):
    status: str
    timestamp: str


class Message(BaseModel):
    term: int
    commit_idx: int
    staged: dict


async def send_heartbeat(receiver_url: str):
    """
    Send a heartbeat to another node.
    """
    while True:
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            response_data = HeartbeatResponse(status="OK", timestamp=current_time)
            response = requests.get(receiver_url)
            if response.status_code == 200:
                print(f"Heartbeat sent from  to {receiver_url}")
            else:
                print(f"Receiver Node at {receiver_url} Unavailable")
        except Exception as e:
            print("Error sending heartbeat:", e)

        await asyncio.sleep(1)


@app.get("/heartbeat", response_model=HeartbeatResponse)
async def heartbeat():
    """
    Heartbeat endpoint to check the API status.
    """
    try:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        response_data = HeartbeatResponse(status="OK", timestamp=current_time)
        return JSONResponse(content=jsonable_encoder(response_data), status_code=200)
    except Exception as e:
        lg.info("Internal Server Error")


@app.on_event('startup')
async def startup_event():
    global current_node

    my_node_name = os.environ.get('NODE_NAME')
    lg.info(f"Im HERE {my_node_name}")

    others_node_name = [node_name for node_name in NODES if node_name != my_node_name]

    current_node = Node(own_host=my_node_name, others_host=others_node_name)


@app.post('/vote_request')
async def vote_request(message: Message):
    global current_node
    term = message.term
    commit_idx = message.commit_idx
    staged = message.staged

    choice, term = current_node


@app.get('/message')
async def get_message():
    return "HvbtrbtrbrtAL"


if __name__ == '__main__':
    lg.info("The launch is starting")
    uvicorn.run(app, host="0.0.0.0", port=8000)
