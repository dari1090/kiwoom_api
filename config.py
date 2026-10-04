import os


def _env_bool(name: str, default: str = 'false') -> bool:
    return os.getenv(name, default).strip().lower() == 'true'


class KiwoomConfig:
    """Configuration for Kiwoom API."""

    VALID_EXCHANGES = ('KRX', 'NXT', 'SOR')

    def __init__(self):
        # Load from environment variables with defaults
        self.appkey: str = os.getenv('KIWOOM_APP_KEY', '')
        self.secretkey: str = os.getenv('KIWOOM_SECRET_KEY', '')
        self.use_mock: bool = _env_bool('KIWOOM_USE_MOCK')

        # API settings
        self.connect_timeout: float = float(os.getenv('KIWOOM_CONNECT_TIMEOUT', '5.0'))
        self.read_timeout: float = float(os.getenv('KIWOOM_READ_TIMEOUT', '30.0'))
        self.max_retries: int = int(os.getenv('KIWOOM_MAX_RETRIES', '3'))
        self.retry_delay: float = float(os.getenv('KIWOOM_RETRY_DELAY', '1.0'))
        self.token_expiry_buffer: int = int(os.getenv('KIWOOM_TOKEN_EXPIRY_BUFFER', '60'))

        # Order safety settings
        self.allow_real_trading: bool = _env_bool('KIWOOM_ALLOW_REAL_TRADING')
        self.max_order_amount: int = int(os.getenv('KIWOOM_MAX_ORDER_AMOUNT', '1000000'))
        self.default_exchange: str = os.getenv('KIWOOM_DEFAULT_EXCHANGE', 'KRX').upper()
        self.dedup_window_seconds: int = int(os.getenv('KIWOOM_DEDUP_WINDOW_SECONDS', '60'))
        self.order_journal_path: str = os.getenv(
            'KIWOOM_ORDER_JOURNAL', '.kiwoom_orders.jsonl'
        )

    @property
    def is_real_trading(self) -> bool:
        """실거래 환경에서 동작 중인지 여부."""
        return not self.use_mock

    def validate(self) -> bool:
        """Validate configuration."""
        if not self.appkey or not self.secretkey:
            raise ValueError("KIWOOM_APP_KEY and KIWOOM_SECRET_KEY must be set")
        if self.default_exchange not in self.VALID_EXCHANGES:
            raise ValueError(
                f"KIWOOM_DEFAULT_EXCHANGE must be one of {self.VALID_EXCHANGES}, "
                f"got {self.default_exchange!r}"
            )
        if self.max_order_amount <= 0:
            raise ValueError("KIWOOM_MAX_ORDER_AMOUNT must be positive")
        return True

    @classmethod
    def from_file(cls, filepath: str) -> 'KiwoomConfig':
        """Load configuration from a JSON file (환경변수보다 우선)."""
        import json

        config = cls()
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        for key, value in data.items():
            if hasattr(config, key):
                setattr(config, key, value)

        return config
