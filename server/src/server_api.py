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
PORT = 8000


LOW_TIMEOUT = 2500
HIGH_TIMEOUT = 5000

REQUESTS_TIMEOUT = 1000
HB_TIME = 1000
MAX_LOG_WAIT = 1000
WAIT_FOR_REPLICATION = 0.0005


class Node:
    def __init__(self, own_host, others_host):
        self.role = 'FOLLOWER'
        self.term = 0
        self.commit_idx = 0
        self.vote_count = 0
        self.own_host = own_host
        self.others_host = others_host
        self.majority = ((len(self.others_host) + 1) // 2) + 1
        self.election_timeout = None
        self.timeout_thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.staged = {}
        self.log = []
        self.committed_log = {}
        self.initial()

    @staticmethod
    def random_timeout():
        return random.randrange(LOW_TIMEOUT, HIGH_TIMEOUT) / 1000

    @staticmethod
    def send_request(host, route, payload):
        destination_url = f"http://{host}:{PORT}/{route}"
        try:
            response = requests.post(destination_url, json=payload, timeout=REQUESTS_TIMEOUT / 1000)
        except Exception as e:
            print(f"Exception occurred at request {e}")
            return

        if response.status_code == 200:
            return response

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
        self.term += 1
        self.vote_count = 0
        self.role = 'CANDIDATE'
        self.initial()
        self.increment_vote()
        # TODO send_vote_req

    def increment_vote(self):
        self.vote_count += 1
        if self.vote_count >= self.majority:
            print(f"{self.own_host} is the LEADER on term {self.term}")
            self.role = 'LEADER'
            self.start_heartbeat()

    def start_heartbeat(self):
        print('Start HB')
        if self.staged:
            self.handle_post_to_log(self.staged)

        for host in self.others_host:
            thread = threading.Thread(target=self.send_heartbeat, args=(host, ))
            thread.start()

    def handle_post_to_log(self, payload):
        print('LOGGING')

        self.lock.acquire()
        self.staged = payload
        waited = 0

        log_message = {
            'term': self.term,
            'leader_id': self.own_host,
            'payload': payload,
            'action': 'log',
            'commit_idx': self.commit_idx
        }

        log_confirmations = [False] * len(self.others_host)
        threading.Thread(target=self.spread_update,
                         args=(log_message, log_confirmations)).start()
        while sum(log_confirmations) + 1 < self.majority:
            waited += WAIT_FOR_REPLICATION
            time.sleep(WAIT_FOR_REPLICATION)

            if waited > MAX_LOG_WAIT / 1000:
                print(f"Waited {MAX_LOG_WAIT} ms, update rejected")
                self.lock.release()
                return False

        commit_message = {
            'term': self.term,
            'leader_id': self.own_host,
            'payload': payload,
            'action': 'commit',
            'commit_idx': self.commit_idx
        }

        self.commit()

        threading.Thread(target=self.spread_update,
                         args=(commit_message, None, self.lock)).start()

    def spread_update(self, message, confirmations=None, lock=None):
        for i, each in enumerate(self.others_host):
            response = self.send_request(each, 'heartbeat', message)
            if response and confirmations:
                confirmations[i] = True
        if lock:
            lock.release()

    def commit(self):
        self.commit_idx += 1
        self.log.append(self.staged)
        key = self.staged['key']
        value = self.staged['value']
        self.committed_log[key] = value
        self.staged = None

    def send_heartbeat(self, follower):
        # відправляємо циклічно хартбіти
        # у випадку втрати лідерства припиняємо

        if self.log:
            self.update_follower_commit_idx(follower)

        route = 'heartbeat'
        message = {'term': self.term, 'leader_id': self.own_host}

        while self.role == 'LEADER':
            start = time.time()
            response = self.send_request(follower, route, message)
            if response:
                self.heartbeat_response_handler(response)

            delta = time.time() - start

            time.sleep((HB_TIME - delta) / 1000)

    def update_follower_commit_idx(self, follower):
        route = 'heartbeat'

        first_message = {'term': self.term, 'leader_id': self.own_host}
        first_response = self.send_request(follower, route, first_message)

        if first_response and first_response.json()['commit_idx'] < self.commit_idx:

            payload = {
                'term': self.term,
                'leader_id': self.own_host,
                'action': 'commit',
                'payload': self.log[-1]
            }

            self.send_request(follower, route, payload)

    def heartbeat_response_handler(self, hb_response):
        # цей метод перевіряє відповідь на хартбіт, якщо у відповіді терм більший за власний, то
        # це означає, що ми вже не лідер і був спліт-брейн, і в решті кластеру вже вібдувається елекшн або є новий лідер

        term = hb_response.json()['term']

        if term > self.term:
            self.term = term
            self.role = 'FOLLOWER'
            self.initial()

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

    decision, term = current_node.vote_decision(term, commit_idx, staged)

    message = {'decision': decision, 'term': term}

    return message


@app.get('/message')
async def get_message():
    return "HvbtrbtrbrtAL"


if __name__ == '__main__':
    lg.info("The launch is starting")
    uvicorn.run(app, host="0.0.0.0", port=8000)
