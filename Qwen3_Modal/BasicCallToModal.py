import modal
from fastapi import Request
from fastapi.responses import JSONResponse

app = modal.App("qwen3-api")

image = (
    modal.Image.debian_slim()
    .pip_install("torch", "transformers", "accelerate", "hf_xet", "peft", "fastapi")
)

@app.function(image=image, gpu="A10G", timeout=300)
@modal.fastapi_endpoint(method="POST")
async def generate(request: Request):
    from transformers import AutoTokenizer, AutoModelForCausalLM
    from peft import PeftModel

    data = await request.json()
    prompt = data.get("prompt", "")

    model_name = "Qwen/Qwen3-4B"
    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(model_name, device_map="auto", trust_remote_code=True)

    # lora_path = "GhostMopey115/lora_model"
    # model = PeftModel.from_pretrained(model, lora_path)
    model.eval()

    system_prompt = "You are a helpful assistant. Answer concisely and do not add any other extra text:\n\n"
    #print prompt in the console
    print(f"Prompt: {prompt}")
    full_prompt = system_prompt + prompt
    print(f"Prompt: {full_prompt}")
    inputs = tokenizer(full_prompt, return_tensors="pt").to(model.device)
    outputs = model.generate(**inputs, max_length=500, do_sample=False, temperature=0.1,)
    response = tokenizer.batch_decode(outputs, skip_special_tokens=True)[0]
    return JSONResponse(content={"response": response})
