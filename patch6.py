with open("src/agent_platform/api/app.py", "r") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "raw_request.state.request_id = request.request_id" in line:
        lines[i] = ""
        break

with open("src/agent_platform/api/app.py", "w") as f:
    f.writelines(lines)
