"""
Tests for sheet_reader.py.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import gspread
from gspread.exceptions import APIError

from sheet_reader import load_watchlist


def make_api_error(status_code: int, message: str) -> APIError:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {
        "error": {
            "code": status_code,
            "message": message,
            "status": "UNAVAILABLE"
        }
    }
    return APIError(response)


class TestLoadWatchlist:
    @patch("sheet_reader.Credentials")
    @patch("sheet_reader.gspread.authorize")
    def test_load_watchlist_success(self, mock_authorize, mock_creds):
        mock_client = MagicMock()
        mock_authorize.return_value = mock_client
        mock_sheet = MagicMock()
        mock_client.open_by_key.return_value = mock_sheet
        mock_ws = MagicMock()
        mock_sheet.worksheet.return_value = mock_ws
        
        mock_ws.get_all_records.return_value = [
            {"ticker": "VWRA.L", "name": "VWRA LSE", "alert_above": "120", "alert_below": "90", "enabled": "TRUE"},
        ]
        
        creds_json = '{"type": "service_account"}'
        rules = load_watchlist("sheet_id_123", creds_json)
        
        assert len(rules) == 1
        assert rules[0].ticker == "VWRA.L"
        assert rules[0].alert_above == 120.0
        assert rules[0].alert_below == 90.0

    @patch("sheet_reader.time.sleep")
    @patch("sheet_reader.Credentials")
    @patch("sheet_reader.gspread.authorize")
    def test_load_watchlist_retry_success(self, mock_authorize, mock_creds, mock_sleep):
        mock_client = MagicMock()
        mock_authorize.return_value = mock_client
        
        # We want the first 2 calls to client.open_by_key to raise 503 APIError
        # and the 3rd to succeed.
        mock_sheet = MagicMock()
        mock_ws = MagicMock()
        mock_sheet.worksheet.return_value = mock_ws
        mock_ws.get_all_records.return_value = [
            {"ticker": "VWRA.L", "name": "VWRA LSE", "alert_above": "120", "alert_below": "90", "enabled": "TRUE"},
        ]
        
        err_503 = make_api_error(503, "The service is currently unavailable.")
        mock_client.open_by_key.side_effect = [err_503, err_503, mock_sheet]
        
        creds_json = '{"type": "service_account"}'
        rules = load_watchlist("sheet_id_123", creds_json)
        
        assert len(rules) == 1
        assert mock_client.open_by_key.call_count == 3
        assert mock_sleep.call_count == 2
        # verify exponential backoff is increasing
        sleep_args = [call[0][0] for call in mock_sleep.call_args_list]
        assert sleep_args[0] >= 1.0
        assert sleep_args[1] > sleep_args[0]

    @patch("sheet_reader.time.sleep")
    @patch("sheet_reader.Credentials")
    @patch("sheet_reader.gspread.authorize")
    def test_load_watchlist_immediate_failure_on_non_transient(self, mock_authorize, mock_creds, mock_sleep):
        mock_client = MagicMock()
        mock_authorize.return_value = mock_client
        
        # 403 Forbidden is non-transient, should fail immediately
        err_403 = make_api_error(403, "Permission denied.")
        mock_client.open_by_key.side_effect = err_403
        
        creds_json = '{"type": "service_account"}'
        with pytest.raises(APIError) as exc_info:
            load_watchlist("sheet_id_123", creds_json)
            
        assert exc_info.value.code == 403
        assert mock_client.open_by_key.call_count == 1
        mock_sleep.assert_not_called()

    @patch("sheet_reader.time.sleep")
    @patch("sheet_reader.Credentials")
    @patch("sheet_reader.gspread.authorize")
    def test_load_watchlist_persistent_failure(self, mock_authorize, mock_creds, mock_sleep):
        mock_client = MagicMock()
        mock_authorize.return_value = mock_client
        
        err_503 = make_api_error(503, "The service is currently unavailable.")
        mock_client.open_by_key.side_effect = err_503
        
        creds_json = '{"type": "service_account"}'
        with pytest.raises(APIError) as exc_info:
            load_watchlist("sheet_id_123", creds_json)
            
        assert exc_info.value.code == 503
        # Should attempt 5 times
        assert mock_client.open_by_key.call_count == 5
        assert mock_sleep.call_count == 4
