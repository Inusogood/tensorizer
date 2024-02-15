
import argparse
import os
import time
import torch
from tensorizer import TensorDeserializer, DecryptionParams
import tensorizer.serialization
from tensorizer.utils import no_init_or_tensor, convert_bytes, get_mem_usage

from transformers import AutoModelForCausalLM, AutoTokenizer, AutoConfig


parser = argparse.ArgumentParser('deserialize')
parser.add_argument('--source', default=None, help='local path or URL')
parser.add_argument('--model-ref', default="EleutherAI/gpt-j-6B")
parser.add_argument('--no-plaid', action='store_true')
parser.add_argument('--lazy-load', action='store_true')
parser.add_argument('--verify-hash', action='store_true')
parser.add_argument('--encryption', action='store_true')
parser.add_argument('--viztracer', action='store_true')
parser.add_argument('--num-readers', type=int, default=1)

args = parser.parse_args()

model_ref = args.model_ref
# To run this at home, swap this with the line below for a smaller example:
# model_ref = "EleutherAI/gpt-neo-125M"
model_name = model_ref.split("/")[-1]

if args.source is None:
    args.source = f"s3://{s3_bucket}/{model_name}.tensors"

tracer = None
if args.viztracer:
    import viztracer
    tracer = viztracer.VizTracer(pid_suffix=True)

decryption_params = None
if args.encryption:
    decryption_params = DecryptionParams.from_string(os.getenv("SUPER_SECRET_STRONG_PASSWORD"))

config = AutoConfig.from_pretrained(model_ref)

# This ensures that the model is not initialized.
with no_init_or_tensor():
    model = AutoModelForCausalLM.from_config(config)

before_mem = get_mem_usage()


# Lazy load the tensors from S3 into the model.
if tracer is not None:
    tracer.start()
start = time.time()
deserializer = TensorDeserializer(
    args.source,
    plaid_mode=not args.no_plaid,
    lazy_load=args.lazy_load,
    encryption=decryption_params,
    num_readers=args.num_readers,
    verify_hash=args.verify_hash
)
deserializer.load_into_module(model)
end = time.time()
if tracer is not None:
    tracer.stop()
    tracer.save()

# Brag about how fast we are.
total_bytes_str = convert_bytes(deserializer.total_tensor_bytes)
duration = end - start
per_second = convert_bytes(deserializer.total_tensor_bytes / duration)
after_mem = get_mem_usage()
deserializer.close()
print(f"Deserialized {total_bytes_str} in {end - start:0.2f}s, {per_second}/s")
print(f"Memory usage before: {before_mem}")
print(f"Memory usage after: {after_mem}")

# Tokenize and generate
model.eval()
tokenizer = AutoTokenizer.from_pretrained(model_ref)
eos = tokenizer.eos_token_id
input_ids = tokenizer.encode(
    "¡Hola! Encantado de conocerte. hoy voy a", return_tensors="pt"
).to("cuda")

with torch.no_grad():
    output = model.generate(
        input_ids, max_new_tokens=50, do_sample=True, pad_token_id=eos
    )

print(f"Output: {tokenizer.decode(output[0], skip_special_tokens=True)}")
perf_stats = tensorizer.serialization.get_perf_stats()
print(f"to CUDA stats: {perf_stats['cuda_bytes']} bytes in {perf_stats['cuda_to_device_secs']}s, {perf_stats['cuda_bytes']/perf_stats['cuda_to_device_secs']/1024/1024/1024:.3f} GiB/s")
print(f"readinto stats: {perf_stats['file_readinto_bytes']} bytes in {perf_stats['file_readinto_secs']}s, {perf_stats['file_readinto_bytes']/perf_stats['file_readinto_secs']/1024/1024/1024:.3f} GiB/s")
