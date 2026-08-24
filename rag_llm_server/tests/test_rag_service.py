import services.rag_service as rag_module


async def test_volc_kb_request_uses_https(monkeypatch):
    requested_urls = []

    class FakeSigner:
        def __init__(self, *args, **kwargs):
            pass

        def add_authorization(self, _config):
            pass

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": {"result_list": []}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            requested_urls.append(url)
            return FakeResponse()

    monkeypatch.setattr(rag_module, "Signer", FakeSigner)
    monkeypatch.setattr(rag_module.httpx, "AsyncClient", FakeClient)
    service = rag_module.RagService()
    service.ak = "ak"
    service.sk = "sk"
    service.account_id = "account"

    await service.retrieve("sensitive query")
    assert requested_urls == [
        "https://api-knowledgebase.mlp.cn-beijing.volces.com/api/knowledge/collection/search_knowledge",
    ]
