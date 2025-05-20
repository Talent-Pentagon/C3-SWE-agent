import modal

# Look up your deployed function by app name and function name
fn = modal.Function.lookup("qwen3-api", "generate")

# Call it remotely
result = fn.remote("Hello, who are you?")
print(result)
