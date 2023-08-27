# Distributed systems

RAFT protocol implementation with Python, FastAPI and Docker.

## Contributors

- Vadym Popyk
- Oleksii Zarembovskyi

## Available API methods

- *GET* /message - This method stands for returning all messages
- *POST* /message - This method stands for passing messages to leader node. This method expects messages in key-value format.
  - Attention: You should send messages only to leader. You can find who is leader in docker containers logs.
  
    'term': self.term, 'leader_id': self.own_host

## Setup

To get started with application, start FastAPI services in docker using the following command:

    docker-compose up

Wait for a few seconds and you should be able to access API endpoints.

To stop running application, terminate it in the console.