"""Provider contracts without credentials, network requests, or model downloads."""
import copy
import json
import types
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from jarvis.providers import (
    ACTION_SCHEMA,
    INTENTS,
    Decider,
    NoRedirect,
    ProviderError,
    normalize_decision,
    ollama_plan,
    post_json,
)


def answer_payload(intent="open_app", probability=0.92):
    return {
        "model": "test-model",
        "answers": {
            "intent": {
                "type": "choice",
                "choice": intent,
                "probabilities": {intent: probability},
                "confidence": 0.01,
                "answer_confidence": 0.02,
            }
        },
        "usage": {"input_tokens": 10, "output_tokens": 0},
    }


class NormalizeDecisionTests(unittest.TestCase):
    def test_selected_probability_is_used_for_both_providers(self):
        for provider in ("laya", "jev"):
            with self.subTest(provider=provider):
                result = normalize_decision(answer_payload(), provider)
                self.assertEqual(result["confidence"], 0.92)
                self.assertEqual(result["confidence_kind"], "selected_label_probability")
                self.assertEqual(result["provider"], provider)

    def test_low_or_unknown_decisions_are_not_scheduled(self):
        for intent, probability in (("open_app", 0.649), ("unknown", 1.0)):
            with self.subTest(intent=intent, probability=probability):
                with self.assertRaises(ProviderError):
                    normalize_decision(answer_payload(intent, probability), "jev")

    def test_boundary_probability_is_accepted(self):
        result = normalize_decision(answer_payload(probability=0.65), "laya")
        self.assertEqual(result["intent"], "open_app")

    def test_nonfinite_out_of_range_and_nonnumeric_values_are_rejected(self):
        invalid = (float("nan"), float("inf"), float("-inf"), -0.1, 1.1, True, "0.9", None)
        for probability in invalid:
            with self.subTest(probability=probability):
                with self.assertRaises(ProviderError):
                    normalize_decision(answer_payload(probability=probability), "jev")

    def test_missing_or_invalid_answer_fields_are_rejected(self):
        cases = [{}, {"answers": {}}, {"answers": {"intent": []}}]
        for field in ("type", "choice", "probabilities"):
            payload = answer_payload()
            del payload["answers"]["intent"][field]
            cases.append(payload)
        for field, value in (("type", "score"), ("choice", "run_shell"), ("probabilities", {})):
            payload = answer_payload()
            payload["answers"]["intent"][field] = value
            cases.append(payload)
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(ProviderError):
                    normalize_decision(payload, "laya")

    def test_routing_model_is_used_when_native_model_is_absent(self):
        payload = answer_payload()
        del payload["model"]
        payload["routing"] = {"model": "multilingual"}
        self.assertEqual(normalize_decision(payload, "laya")["model"], "multilingual")

    def test_null_routing_metadata_fails_with_controlled_error(self):
        payload = answer_payload()
        del payload["model"]
        payload["routing"] = None
        with self.assertRaises(ProviderError):
            normalize_decision(payload, "laya")


class DeciderTests(unittest.TestCase):
    def test_rules_needs_no_external_provider(self):
        with patch("jarvis.providers.post_json") as request:
            result = Decider().decide("open Safari", "open_app")
        request.assert_not_called()
        self.assertEqual(result["intent"], "open_app")
        self.assertIsNone(result["confidence"])

    def test_invalid_provider_is_rejected(self):
        with self.assertRaises(ProviderError):
            Decider("auto-upload")

    def test_jev_missing_key_never_attempts_request(self):
        with patch.dict("os.environ", {}, clear=True), patch("jarvis.providers.post_json") as request:
            with self.assertRaisesRegex(ProviderError, "TYPESAFE_API_KEY"):
                Decider("jev").decide("open Safari", "open_app")
        request.assert_not_called()

    def test_jev_request_uses_official_shape_and_server_side_authentication(self):
        environment = {"TYPESAFE_API_KEY": "test-only-key", "JARVIS_JEV_MODEL": "jev-1.13.0"}
        with patch.dict("os.environ", environment, clear=True), patch(
            "jarvis.providers.post_json", return_value=answer_payload()
        ) as request:
            result = Decider("jev").decide("open Safari", "open_app")
        url, body, headers = request.call_args.args
        self.assertEqual(url, "https://api.typesafe.ai/v1/systemone")
        self.assertEqual(headers, {"Authorization": "Bearer test-only-key"})
        self.assertEqual(body["model"], "jev-1.13.0")
        self.assertEqual(body["state"], {"request": "open Safari"})
        self.assertEqual(body["questions"]["intent"]["type"], "choice")
        self.assertEqual(body["questions"]["intent"]["criteria"], INTENTS)
        self.assertEqual(result["provider"], "jev")
        self.assertNotIn("test-only-key", json.dumps(body))

    def test_laya_is_loaded_lazily_once_and_returns_plain_dict(self):
        router = MagicMock()
        router.predict.return_value = answer_payload()
        constructor = MagicMock(return_value=router)
        module = types.ModuleType("laya")
        module.Router = constructor
        with patch.dict("sys.modules", {"laya": module}), patch("jarvis.providers.post_json") as request:
            decider = Decider("laya")
            constructor.assert_not_called()
            for _ in range(2):
                result = decider.decide("open Safari", "open_app")
        constructor.assert_called_once_with()
        self.assertEqual(router.predict.call_count, 2)
        self.assertEqual(router.predict.call_args.args[0], {"request": "open Safari"})
        self.assertEqual(result["provider"], "laya")
        request.assert_not_called()

    def test_laya_failure_does_not_fall_back_to_network(self):
        module = types.ModuleType("laya")
        module.Router = MagicMock(side_effect=RuntimeError("private path detail"))
        with patch.dict("sys.modules", {"laya": module}), patch("jarvis.providers.post_json") as request:
            with self.assertRaises(ProviderError) as error:
                Decider("laya").decide("open Safari", "open_app")
        request.assert_not_called()
        self.assertNotIn("private path detail", str(error.exception))

    def test_missing_laya_dependency_has_installation_guidance(self):
        with patch.dict("sys.modules", {"laya": None}):
            with self.assertRaisesRegex(ProviderError, r"\.\[laya\]"):
                Decider("laya").decide("open Safari", "open_app")


class HttpContractTests(unittest.TestCase):
    def opener_with_bytes(self, payload):
        response = MagicMock()
        response.read.return_value = payload
        response.__enter__.return_value = response
        opener = MagicMock()
        opener.open.return_value = response
        return opener, response

    def test_json_body_auth_and_timeout_use_redirect_free_proxy_free_transport(self):
        opener, response = self.opener_with_bytes(b'{"ok": true}')
        with patch("jarvis.providers.build_opener", return_value=opener) as build:
            result = post_json("https://example.test/api", {"request": "hi"},
                               {"Authorization": "Bearer test-key"}, timeout=7)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(json.loads(request.data), {"request": "hi"})
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(opener.open.call_args.kwargs, {"timeout": 7})
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], NoRedirect)
        self.assertEqual(result, {"ok": True})
        response.read.assert_called_once_with(1_048_577)

    def test_redirect_handler_declines_new_destination(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.test"))

    def test_invalid_json_nonobject_and_oversized_responses_are_rejected(self):
        for payload in (b"not json", b"[]", b"null", b"x" * 1_048_577):
            with self.subTest(length=len(payload)):
                opener, _ = self.opener_with_bytes(payload)
                with patch("jarvis.providers.build_opener", return_value=opener):
                    with self.assertRaises(ProviderError):
                        post_json("http://127.0.0.1/test", {})

    def test_network_errors_become_credential_free_user_errors(self):
        errors = [
            URLError("sensitive detail"), TimeoutError("sensitive detail"),
            HTTPError("https://example.test", 401, "sensitive detail", {}, None),
        ]
        for failure in errors:
            if isinstance(failure, HTTPError):
                self.addCleanup(failure.close)
            with self.subTest(failure=type(failure).__name__):
                opener = MagicMock()
                opener.open.side_effect = failure
                with patch("jarvis.providers.build_opener", return_value=opener):
                    with self.assertRaises(ProviderError) as error:
                        post_json("https://example.test", {})
                self.assertNotIn("sensitive detail", str(error.exception))


class OllamaContractTests(unittest.TestCase):
    def setUp(self):
        self.config = {"apps": {"Safari": "Safari"}, "contacts": {"Alex": "+15555550123"}}

    def test_local_chat_uses_schema_and_explicit_model(self):
        actions = [{"kind": "open_app", "args": {"app": "Safari"}}]
        with patch.dict("os.environ", {"JARVIS_OLLAMA_MODEL": "test-local"}, clear=True), patch(
            "jarvis.providers.post_json", return_value={"message": {"content": json.dumps({"actions": actions})}}
        ) as request:
            result = ollama_plan("bring up my browser", self.config, "open_app")
        url, body = request.call_args.args
        self.assertEqual(url, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(body["model"], "test-local")
        self.assertEqual(body["format"], ACTION_SCHEMA)
        self.assertEqual(body["options"], {"temperature": 0})
        self.assertFalse(body["stream"])
        self.assertEqual(body["messages"][-1], {"role": "user", "content": "bring up my browser"})
        self.assertEqual(result, actions)

    def test_schema_does_not_offer_arbitrary_shell_execution(self):
        kinds = ACTION_SCHEMA["properties"]["actions"]["items"]["properties"]["kind"]["enum"]
        self.assertNotIn("run_shell", kinds)
        self.assertNotIn("exec", kinds)
        self.assertFalse(ACTION_SCHEMA["additionalProperties"])

    def test_bad_model_envelopes_and_plan_shapes_are_rejected(self):
        malformed = [{}, {"message": {}}, {"message": {"content": "not-json"}}]
        for value in (None, [], {"actions": {}}, {"actions": [], "shell": "bad"}):
            malformed.append({"message": {"content": json.dumps(value)}})
        for response in malformed:
            with self.subTest(response=response):
                with patch("jarvis.providers.post_json", return_value=copy.deepcopy(response)):
                    with self.assertRaises(ProviderError):
                        ollama_plan("open Safari", self.config, "open_app")


if __name__ == "__main__":
    unittest.main()
