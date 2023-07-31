import os
import time
import logging as lg

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional
import requests
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
WAIT_FOR_REPLICATION = 5


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
            return

        if response.status_code == 200:
            return response

    def initial(self):
        self.set_election_timeout()

        if self.timeout_thread and self.timeout_thread.is_alive():
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
        self.send_vote_request()

    def increment_vote(self):
        self.vote_count += 1
        if self.vote_count >= self.majority:
            lg.info(f"{self.own_host} is the LEADER on term {self.term}")
            self.role = 'LEADER'
            self.start_heartbeat()

    def start_heartbeat(self):
        lg.info('Start HB')
        if self.staged:
            self.handle_post_to_log(self.staged)

        for host in self.others_host:
            thread = threading.Thread(target=self.send_heartbeat, args=(host, ))
            thread.start()

    def handle_post_to_log(self, payload):

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
            time.sleep(WAIT_FOR_REPLICATION/1000)

            if waited > MAX_LOG_WAIT:
                lg.info(f"Waited {MAX_LOG_WAIT} ms, update rejected")
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

        lg.info(f"This is a commit message: {commit_message}")

        threading.Thread(target=self.spread_update,
                         args=(commit_message, None, self.lock)).start()

        return True

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

            lg.info(f"SEND {message}")
            response = self.send_request(follower, route, message)

            message = {'term': self.term, 'leader_id': self.own_host}
            if response:
                lg.info(f"heartbeat key {response}")
                follower_commit_idx = self.heartbeat_response_handler(response)

                if follower_commit_idx < self.commit_idx:
                    lg.info(f"COMMIT IDX MISMATCH "
                            f"{follower_commit_idx}, {self.commit_idx}, {len(self.log)}")

                    message['payload'] = self.log[follower_commit_idx]
                    message['action'] = 'commit'
                    message['commit_idx'] = self.commit_idx

            delta = time.time() - start

            time.sleep((HB_TIME - delta) / 1000)

    def update_follower_commit_idx(self, follower):
        lg.info('ENTER update follower')
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

        lg.info(hb_response.json()['commit_idx'])

        if term > self.term:
            self.term = term
            self.role = 'FOLLOWER'
            self.initial()
        commit_idx = hb_response.json()['commit_idx']
        return commit_idx

    def vote_decision(self, term, commit_idx, staged):

        if self.term < term and self.commit_idx <= commit_idx and (staged or (self.staged == staged)):
            self.set_election_timeout()
            self.term = term
            return True, self.term
        else:
            return False, self.term

    def send_vote_request(self):
        for voter in self.others_host:
            threading.Thread(target=self.ask_for_vote,
                             args=(voter, self.term)).start()

    def ask_for_vote(self, voter, term):

        message = {
            'term': term,
            'commit_idx': self.commit_idx,
            'staged': self.staged
        }

        route = 'vote_request'

        while self.role == 'CANDIDATE' and self.term == term:
            reply = self.send_request(voter, route, message)
            if reply:
                decision = reply.json()['decision']
                if decision and self.role == 'CANDIDATE':
                    self.increment_vote()
                elif not decision:
                    term = reply.json()['term']
                    if term > self.term:
                        self.term = term
                        self.role = 'FOLLOWER'
                break

    def handle_get(self, ):
        lg.info('GET LOG')

        return self.committed_log

    def heartbeat_follower(self, msg):
        term = msg['term']

        if self.term <= term:
            self.set_election_timeout()

            if self.role == 'CANDIDATE':
                self.role = 'FOLLOWER'
            elif self.role == 'LEADER':
                self.role = 'FOLLOWER'
                self.initial()
            if self.term < term:
                self.term = term

            if 'action' in msg:
                lg.info('RECEIVED ACTION')

                action = msg['action']

                if action == 'log':
                    payload = msg['payload']
                    self.staged = payload
                elif self.commit_idx <= msg['commit_idx']:
                    self.staged = msg['payload']
                    self.commit()

        return self.term, self.commit_idx


current_node: Node | None = None


class VoteMessage(BaseModel):
    term: int
    commit_idx: int
    staged: Optional[dict] = None


class Message(BaseModel):
    key: str
    value: str


class HeartbeatMessage(BaseModel):
    term: int
    leader_id: str
    payload: Optional[dict] = None
    action: Optional[str] = None
    commit_idx: Optional[int] = None


@app.on_event('startup')
async def startup_event():
    global current_node

    my_node_name = os.environ.get('NODE_NAME')
    lg.info(f"Im HERE {my_node_name}")

    others_node_name = [node_name for node_name in NODES if node_name != my_node_name]

    current_node = Node(own_host=my_node_name, others_host=others_node_name)


@app.post('/vote_request')
async def vote_request(message: VoteMessage):
    global current_node
    term = message.term
    commit_idx = message.commit_idx
    staged = message.staged

    decision, term = current_node.vote_decision(term, commit_idx, staged)

    message = {'decision': decision, 'term': term}

    return message


@app.get('/message')
async def get_message():
    global current_node
    result = current_node.handle_get()
    if result:
        return {'status': 'success', 'payload': result}
    else:
        return {'status': 'fail', 'payload': []}


@app.post('/message')
async def post_message(message: Message):
    global current_node

    payload = {
        'key': message.key,
        'value': message.value
    }

    result = current_node.handle_post_to_log(payload)
    if result:
        return {'status': 'success'}
    else:
        return {'status': 'fail'}


@app.post('/heartbeat')
def heartbeat(message: HeartbeatMessage):
    global current_node

    payload = {
        'term': message.term,
        'leader_id': message.leader_id,
    }

    if message.payload:
        payload['payload'] = message.payload
    if message.action:
        payload['action'] = message.action
    if message.commit_idx is not None:
        payload['commit_idx'] = message.commit_idx

    term, commit_idx = current_node.heartbeat_follower(payload)
    return {"term": term, "commit_idx": commit_idx}


if __name__ == '__main__':
    lg.info("The launch is starting")
    uvicorn.run(app, host="0.0.0.0", port=8000)
