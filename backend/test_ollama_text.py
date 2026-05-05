import requests

response = requests.post(
    "http://localhost:11434/api/chat",
    json={
        "model": "llama3.1:8b",
        "messages": [
            {
                "role": "user",
                "content": "Reply only OK if you are working."
            }
        ],
        "stream": False,
    },
    timeout=120,
)

response.raise_for_status()
print(response.json()["message"]["content"])