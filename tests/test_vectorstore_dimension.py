import unittest
from unittest.mock import patch

import vectorstore


class VectorDimensionTests(unittest.TestCase):
    def test_dimension_conflict_has_actionable_message(self):
        class Collection:
            def add(self, **kwargs):
                raise RuntimeError("Collection expecting embedding with dimension of 1024, got 256")

        with patch.object(vectorstore, "get_collection", return_value=Collection()):
            with self.assertRaisesRegex(ValueError, "reset_collection"):
                vectorstore.add_documents(["x"], [[0.0, 1.0]], [{}], ["id"])


if __name__ == "__main__":
    unittest.main()
