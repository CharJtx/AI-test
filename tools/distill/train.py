"""Stage 4: QLoRA rank-32 fine-tune.

This stage is a thin wrapper that emits the correct axolotl/TRL invocation.
We do NOT rewrite training code — use upstream Axolotl for correctness.

Recommended stack:
    OpenAccess-AI-Collective/axolotl (YAML-configured QLoRA)
    huggingface/trl for DPO/KTO polishing (optional)

Typical run on 4xRTX 4090 for 30k turns, rank-32, 3 epochs: ~3-6 hours.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from textwrap import dedent


AXOLOTL_CONFIG_TEMPLATE = dedent(
    """
    base_model: {base}
    model_type: LlamaForCausalLM
    tokenizer_type: AutoTokenizer

    load_in_4bit: true
    adapter: qlora
    lora_r: {rank}
    lora_alpha: {alpha}
    lora_dropout: 0.05
    lora_target_modules:
      - q_proj
      - k_proj
      - v_proj
      - o_proj
      - gate_proj
      - up_proj
      - down_proj

    datasets:
      - path: {data_path}
        type: sharegpt
        conversation: chatml

    sequence_len: 4096
    sample_packing: true
    pad_to_sequence_len: true

    gradient_accumulation_steps: 4
    micro_batch_size: 2
    num_epochs: {epochs}
    optimizer: paged_adamw_8bit
    lr_scheduler: cosine
    learning_rate: 0.0002

    bf16: auto
    tf32: true
    gradient_checkpointing: true
    flash_attention: true

    warmup_ratio: 0.03
    evals_per_epoch: 1
    saves_per_epoch: 1
    save_total_limit: 2
    output_dir: ./work/train_output/{creator}
    """
).strip()


def run(args: argparse.Namespace) -> int:
    out_dir = Path("work/train_output") / args.creator
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = out_dir / "axolotl.yaml"
    cfg_path.write_text(
        AXOLOTL_CONFIG_TEMPLATE.format(
            base=args.base,
            rank=args.rank,
            alpha=args.rank * 2,
            data_path=str(Path(args.data) / args.creator / "sharegpt.jsonl"),
            epochs=args.epochs,
            creator=args.creator,
        ),
        encoding="utf-8",
    )

    print(f"[train] wrote axolotl config: {cfg_path}")
    print("\n--- Run on the GPU server ---")
    print("# With 4 dedicated 4090s (weights fp16, QLoRA merges in bf16):")
    print(
        f"""docker run --rm --gpus '"device=0,1,4,5"' \\
  --ipc=host -v "$PWD:/workspace" -w /workspace \\
  axolotlai/axolotl:main-latest \\
  accelerate launch --config_file /workspace/accelerate_fsdp.yaml \\
    -m axolotl.cli.train {cfg_path}"""
    )
    print("\n# The resulting LoRA lands at:")
    print(f"#   {out_dir}/adapter_model.safetensors + adapter_config.json")
    print("# Copy into skills/{creator}/lora/ and update meta.json `lora_name`.")
    return 0
