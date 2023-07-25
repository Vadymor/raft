import os
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


lg.basicConfig(level=lg.INFO)

app = FastAPI()

session: aiohttp.ClientSession | None = None

NODES = ('node-1', 'node-2', 'node-3')


class HeartbeatResponse(BaseModel):
    status: str
    timestamp: str


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
    global session
    session = aiohttp.ClientSession()

    my_node_name = os.environ.get('NODE_NAME')
    lg.info(f"Im HERE {my_node_name}")

    receiver_urls = [f"http://{node}:{8000}/heartbeat" for node in NODES if node != my_node_name]

    for url in receiver_urls:
        task = asyncio.create_task(send_heartbeat(url))


@app.on_event('shutdown')
async def shutdown_event():
    await session.close()


@app.get('/message')
async def get_message():
    return "HvbtrbtrbrtAL"


if __name__ == '__main__':
    lg.info("The launch is starting")
    uvicorn.run(app, host="0.0.0.0", port=8000)
