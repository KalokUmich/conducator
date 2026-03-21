from unittest.mock import patch, MagicMock
from app.service import OrderService, process_payment

class TestOrderService:
    def test_create_order(self):
        svc = OrderService()
        result = svc.create_order("u1", 100)
        assert result is not None

    @patch("app.service.find_user")
    def test_create_order_mocked(self, mock_find):
        mock_find.return_value = MagicMock(email="a@b.com")
        svc = OrderService()
        result = svc.create_order("u1", 100)
        assert result is not None
        mock_find.assert_called_once_with("u1")

    @patch("app.service.save_order")
    @patch("app.service.find_user")
    def test_create_order_double_mock(self, mock_find, mock_save):
        mock_find.return_value = MagicMock(email="x@y.com")
        mock_save.return_value = {"id": "u1", "amount": 50}
        svc = OrderService()
        result = svc.create_order("u1", 50)
        assert result["amount"] == 50

def test_process_payment():
    result = process_payment("tok_123", 99)
    assert result is not None
