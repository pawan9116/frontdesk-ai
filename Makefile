.PHONY: install setup mcp agent jaeger test clean

# Install dependencies
install:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt

# One-time SIP trunk + dispatch rule setup
setup:
	.venv/bin/python setup_infra.py

# Start MCP server
mcp:
	.venv/bin/python src/mcp_server.py

# Start agent
agent:
	.venv/bin/python src/agent.py dev

# Start Jaeger (requires Docker)
jaeger:
	docker run -d --name jaeger -p 16686:16686 -p 4318:4318 jaegertracing/jaeger:latest
	@echo "Jaeger UI: http://localhost:16686"

# Run tests
test:
	.venv/bin/python -m pytest tests/test_scenarios.py -v

# Stop Jaeger
clean:
	-docker stop jaeger && docker rm jaeger
