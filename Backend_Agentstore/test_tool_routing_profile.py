import unittest

from tool_routing_profile import merge_routing_profiles, normalize_routing_profile, routing_profile_template


class ToolRoutingProfileTests(unittest.TestCase):
    def test_exposes_a_universal_configuration_template(self):
        template = routing_profile_template()
        keys = {field["key"] for field in template["fields"]}

        self.assertIn("purpose", keys)
        self.assertIn("use_when", keys)
        self.assertIn("do_not_use_when", keys)
        self.assertIn("example_requests", keys)
        self.assertIn("counterexamples", keys)
        self.assertIn("does not change permissions", template["description"])

    def test_normalizes_aliases_lists_and_bounds_untrusted_admin_content(self):
        profile = normalize_routing_profile(
            {
                "purpose": "  Find   current customer records  ",
                "useWhen": "Customer lookup\nAccount status",
                "avoidWhen": ["General product guidance", "general product guidance"],
                "exampleRequests": ["x" * 400] * 20,
                "freshness": "LIVE",
                "ignored": "not planner metadata",
            }
        )

        self.assertEqual(profile["purpose"], "Find current customer records")
        self.assertEqual(profile["use_when"], ["Customer lookup", "Account status"])
        self.assertEqual(profile["do_not_use_when"], ["General product guidance"])
        self.assertEqual(len(profile["example_requests"]), 1)
        self.assertEqual(len(profile["example_requests"][0]), 300)
        self.assertEqual(profile["freshness"], "live")
        self.assertNotIn("ignored", profile)

    def test_merges_broad_and_specific_profiles_without_duplicate_examples(self):
        profile = merge_routing_profiles(
            {"purpose": "Broad server purpose", "use_when": ["Search records"]},
            {"purpose": "Specific tool purpose", "use_when": ["search records", "Find a person"]},
        )

        self.assertEqual(profile["purpose"], "Specific tool purpose")
        self.assertEqual(profile["use_when"], ["Search records", "Find a person"])


if __name__ == "__main__":
    unittest.main()
