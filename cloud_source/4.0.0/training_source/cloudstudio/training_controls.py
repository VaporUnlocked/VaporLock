"""Optimizer and repeatability controls shared by the training loop and tests."""
from __future__ import annotations

from contextlib import contextmanager
import math
import os

import torch


def make_optimizer(model, config):
    """AdamW with explicit moments and optional bias exclusion from decay."""
    if config.weight_decay_bias:
        parameters = model.parameters()
    else:
        decayed, biases = [], []
        for name, parameter in model.named_parameters():
            (biases if name.endswith(".bias") else decayed).append(parameter)
        parameters = [{"params": decayed, "weight_decay": config.weight_decay},
                      {"params": biases, "weight_decay": 0.0}]
    return torch.optim.AdamW(parameters, lr=config.learning_rate,
        betas=(config.adam_beta1, config.adam_beta2), eps=config.adam_epsilon,
        weight_decay=config.weight_decay)


def learning_rate_for_update(config, update_index: int, total_updates: int,
                             updates_per_epoch: int) -> float:
    """LR for the next optimizer update, not a minibatch or teacher-box epoch.

    The first warmup update uses start_factor * lr0 and the last uses lr0.
    Cosine starts after warmup and reaches final_factor * lr0 on the final
    planned update when at least two decay updates exist (a single update uses
    lr0). Extending a resumed run keeps its original schedule horizon
    and holds the final LR after that horizon instead of rewriting prior steps.
    """
    warmup = config.lr_warmup_epochs * updates_per_epoch
    if warmup and update_index < warmup:
        fraction = update_index / max(warmup - 1, 1)
        factor = config.lr_warmup_start_factor + (1 - config.lr_warmup_start_factor) * fraction
    elif config.scheduler == "cosine":
        fraction = min(1.0, max(0.0, (update_index - warmup) / max(total_updates - warmup - 1, 1)))
        factor = config.lr_final_factor + (1 - config.lr_final_factor) * (1 + math.cos(math.pi * fraction)) / 2
    else:
        factor = 1.0
    return config.learning_rate * factor


@contextmanager
def training_numerics(config):
    """Scope strict deterministic settings to this run, restoring process flags."""
    previous = {"deterministic": torch.are_deterministic_algorithms_enabled(),
                "warn_only": torch.is_deterministic_algorithms_warn_only_enabled(),
                "cudnn_deterministic": torch.backends.cudnn.deterministic,
                "cudnn_benchmark": torch.backends.cudnn.benchmark,
                "cublas_workspace": os.environ.get("CUBLAS_WORKSPACE_CONFIG")}
    if config.deterministic:
        os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    try:
        torch.use_deterministic_algorithms(config.deterministic, warn_only=False)
        torch.backends.cudnn.deterministic = config.deterministic
        if config.deterministic:
            torch.backends.cudnn.benchmark = False
        yield
    except RuntimeError as exc:
        if config.deterministic and "deterministic" in str(exc).lower():
            raise RuntimeError(
                "Strict deterministic training could not run an operation on this PyTorch/device "
                "combination. The deterministic setting was not ignored. Choose CPU, or explicitly "
                "disable Deterministic algorithms if repeatability is not required. Original error: "
                + str(exc)) from exc
        raise
    finally:
        torch.use_deterministic_algorithms(previous["deterministic"], warn_only=previous["warn_only"])
        torch.backends.cudnn.deterministic = previous["cudnn_deterministic"]
        torch.backends.cudnn.benchmark = previous["cudnn_benchmark"]
        if previous["cublas_workspace"] is None:
            os.environ.pop("CUBLAS_WORKSPACE_CONFIG", None)
        else:
            os.environ["CUBLAS_WORKSPACE_CONFIG"] = previous["cublas_workspace"]
