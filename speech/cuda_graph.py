"""Replay CosyVoice's single-token Qwen decoder without Python launch overhead.

Keeps the original weights, autocast precision, sampling, and ten-step acoustic
renderer. A static KV cache is reset per sentence; long sequences fall back to
upstream dynamic-cache decoding. One synthesis request owns this adapter.
"""


class CudaGraphDecoder:
    def __init__(self, encoder, max_tokens: int = 4096):
        import torch
        from transformers import StaticCache
        self.torch = torch
        self.encoder = encoder
        self.original = encoder.forward_one_step
        self.model = encoder.model.model
        self.max_tokens = max_tokens
        self.position = 0
        self.cache = StaticCache(config=self.model.config, max_batch_size=1,
                                 max_cache_len=max_tokens, device='cuda', dtype=torch.float16)
        self.inputs = torch.zeros(1, 1, self.model.config.hidden_size, device='cuda', dtype=torch.float32)
        self.cache_position = torch.zeros(1, device='cuda', dtype=torch.long)
        self.graph = torch.cuda.CUDAGraph()
        stream = torch.cuda.Stream()
        stream.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(stream), torch.inference_mode(), torch.autocast('cuda', dtype=torch.float16, cache_enabled=False):
            for _ in range(3):
                self._step()
            stream.synchronize()
            with torch.cuda.graph(self.graph, stream=stream):
                self.output = self._step()
        torch.cuda.current_stream().wait_stream(stream)
        self.cache.reset()
        torch.cuda.synchronize()

    def _step(self):
        return self.model(inputs_embeds=self.inputs, past_key_values=self.cache,
                          cache_position=self.cache_position, use_cache=True,
                          output_hidden_states=False, return_dict=True).last_hidden_state

    def __call__(self, xs, masks, cache=None):
        torch = self.torch
        if cache is None:
            if xs.shape[1] >= self.max_tokens:
                return self.original(xs, masks, cache)
            self.cache.reset()
            self.position = xs.shape[1]
            output = self.model(inputs_embeds=xs, past_key_values=self.cache,
                                cache_position=torch.arange(self.position, device=xs.device),
                                use_cache=True, return_dict=True)
            return output.last_hidden_state, self.cache
        if cache is not self.cache:
            return self.original(xs, masks, cache)
        if self.position >= self.max_tokens:
            from transformers import DynamicCache
            dynamic = DynamicCache.from_legacy_cache(tuple(
                (key.clone(), value.clone()) for key, value in zip(self.cache.key_cache, self.cache.value_cache)))
            return self.original(xs, masks, dynamic)
        self.inputs.copy_(xs)
        self.cache_position.fill_(self.position)
        self.graph.replay()
        self.position += 1
        return self.output.clone(), self.cache

    def validate(self):
        """Compare eager and graph hidden states before enabling the optimization."""
        torch = self.torch
        with torch.inference_mode(), torch.autocast('cuda', dtype=torch.float16):
            # Preserve sampling RNG state; this validation never changes a voice.
            with torch.random.fork_rng(devices=[torch.cuda.current_device()]):
                torch.manual_seed(42)
                xs = torch.randn(1, 8, self.model.config.hidden_size, device='cuda')
                mask = torch.ones(1, 8, 8, device='cuda', dtype=torch.bool).tril()
                eager, eager_cache = self.original(xs, mask, None)
                accelerated, graph_cache = self(xs, mask, None)
                torch.testing.assert_close(accelerated, eager, atol=0.02, rtol=0.01)
                for _ in range(3):
                    xs = torch.randn(1, 1, self.model.config.hidden_size, device='cuda')
                    mask = torch.ones(1, 1, 1, device='cuda', dtype=torch.bool)
                    eager, eager_cache = self.original(xs, mask, eager_cache)
                    accelerated, graph_cache = self(xs, mask, graph_cache)
                    torch.testing.assert_close(accelerated, eager, atol=0.02, rtol=0.01)
        self.cache.reset()
        torch.cuda.synchronize()
