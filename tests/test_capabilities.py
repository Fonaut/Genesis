"""
tests/test_capabilities.py — Tests for Genesis Capability Manifest and Truthfulness Grounding.

Tests:
1. Manifest completeness: verifies that verified, unavailable, and aspirational capabilities are defined.
2. Boundaries presence: ensures cameras, facial recognition, biometrics, and room energy are marked unavailable.
3. Prompt grounding: verifies that the capability prompt embeds clear instructions to distinguish
   reality from aspiration and state physical boundaries honestly.
"""

import unittest
from core.capabilities import (
    ASPIRATIONS,
    UNAVAILABLE_CAPABILITIES,
    VERIFIED_CAPABILITIES,
    get_capability_manifest,
    get_capability_prompt,
)


class TestCapabilities(unittest.TestCase):
    """Unit tests for the Genesis capability manifest."""

    def test_manifest_structure(self):
        """Manifest contains verified, unavailable, and aspirations sections."""
        manifest = get_capability_manifest()
        self.assertIn("verified", manifest)
        self.assertIn("unavailable", manifest)
        self.assertIn("aspirations", manifest)
        self.assertIsInstance(manifest["verified"], dict)
        self.assertIsInstance(manifest["unavailable"], list)
        self.assertIsInstance(manifest["aspirations"], list)

    def test_verified_capabilities_present(self):
        """Microphone, TTS, memory, and tools are registered as verified."""
        self.assertIn("voice_input", VERIFIED_CAPABILITIES)
        self.assertIn("voice_output", VERIFIED_CAPABILITIES)
        self.assertIn("memory", VERIFIED_CAPABILITIES)
        self.assertIn("local_tools", VERIFIED_CAPABILITIES)

    def test_unavailable_hardware_boundaries_explicit(self):
        """Cameras, biometrics, and room acoustics are explicitly declared unavailable."""
        unavailable_text = " ".join(UNAVAILABLE_CAPABILITIES).lower()
        self.assertIn("camera", unavailable_text)
        self.assertIn("facial", unavailable_text)
        self.assertIn("biometric", unavailable_text)
        self.assertIn("room", unavailable_text)

    def test_capability_prompt_content(self):
        """Capability prompt contains clear directives for honesty and sensory limits."""
        prompt = get_capability_prompt()
        self.assertIn("NO cameras", prompt)
        self.assertIn("NO biometric sensors", prompt)
        self.assertIn("room energy", prompt)
        self.assertIn("distinguish between what you wish for and the physical reality", prompt)


if __name__ == "__main__":
    unittest.main()
