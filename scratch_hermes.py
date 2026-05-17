import json
from run_agent import AIAgent
from tools.registry import registry as global_registry

def dummy_handler(args, **kwargs):
    return json.dumps({"result": args.get("query", "none")})

global_registry.register(
    name="myj.goods_search",
    toolset="test",
    schema={
        "name": "myj.goods_search",
        "description": "Search goods",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}
    },
    handler=dummy_handler,
    is_async=False,
    description="Search goods",
    emoji="🔧"
)

agent = AIAgent(
    provider="openai",
    model="gpt-4o-mini",
    enabled_toolsets=["test"],
    max_iterations=2
)

result = agent.run_conversation(
    user_message="Find me low sugar drinks",
    system_message="Use the goods_search tool."
)

print(json.dumps(result, indent=2))
