import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import _build_portfolio_industry_tag


class FundIndustryClassificationTest(unittest.TestCase):
    def test_etf_feeder_uses_name_topic_without_holdings(self):
        tag = _build_portfolio_industry_tag({
            "fund_name": "华夏中证半导体ETF联接A",
            "fund_type": "指数型",
        })
        self.assertEqual(tag["name"], "半导体")
        self.assertEqual(tag["basis"], "index_topic")

    def test_broad_index_uses_name_without_holdings(self):
        tag = _build_portfolio_industry_tag({
            "fund_name": "易方达沪深300指数增强A",
            "fund_type": "指数型",
        })
        self.assertEqual(tag["name"], "宽基指数")
        self.assertEqual(tag["basis"], "broad_index_name")

    def test_qdii_nasdaq_uses_market_topic_without_holdings(self):
        tag = _build_portfolio_industry_tag({
            "fund_name": "广发纳斯达克100指数QDII",
            "fund_type": "QDII",
        })
        self.assertEqual(tag["name"], "美股科技")
        self.assertEqual(tag["basis"], "market_region")

    def test_four_same_industry_holdings_without_ratio_use_count(self):
        holdings = [
            {"code": "000001", "name": "A", "industry": "新能源"},
            {"code": "000002", "name": "B", "industry": "新能源"},
            {"code": "000003", "name": "C", "industry": "新能源"},
            {"code": "000004", "name": "D", "industry": "新能源"},
            {"code": "000005", "name": "E", "industry": "医药"},
        ]
        tag = _build_portfolio_industry_tag({"stock_codes_new": holdings})
        self.assertEqual(tag["name"], "新能源")
        self.assertEqual(tag["basis"], "holding_count")
        self.assertFalse(tag["has_weight"])

    def test_three_same_industry_with_dominant_ratio_use_weight(self):
        holdings = [
            {"code": "000001", "name": "A", "industry": "医药", "ratio": 15},
            {"code": "000002", "name": "B", "industry": "医药", "ratio": 15},
            {"code": "000003", "name": "C", "industry": "医药", "ratio": 15},
            {"code": "000004", "name": "D", "industry": "消费", "ratio": 10},
            {"code": "000005", "name": "E", "industry": "新能源", "ratio": 5},
        ]
        tag = _build_portfolio_industry_tag({"stock_codes_new": holdings})
        self.assertEqual(tag["name"], "医药")
        self.assertEqual(tag["basis"], "holding_weight")
        self.assertTrue(tag["has_weight"])

    def test_mixed_when_no_name_topic_and_holdings_are_spread(self):
        holdings = [
            {"code": "000001", "name": "A", "industry": "医药"},
            {"code": "000002", "name": "B", "industry": "消费"},
            {"code": "000003", "name": "C", "industry": "新能源"},
            {"code": "000004", "name": "D", "industry": "银行"},
        ]
        tag = _build_portfolio_industry_tag({"stock_codes_new": holdings})
        self.assertEqual(tag["name"], "混合型")
        self.assertEqual(tag["basis"], "mixed")


if __name__ == "__main__":
    unittest.main()
