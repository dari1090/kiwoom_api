import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
import json


class KiwoomOAuth:
    """Kiwoom OpenAPI OAuth authentication module. (API: au10001)"""

    PROD_BASE_URL = "https://api.kiwoom.com"
    MOCK_BASE_URL = "https://mockapi.kiwoom.com"
    TOKEN_ENDPOINT = "/oauth2/token"

    def __init__(self, appkey: str, secretkey: str, use_mock: bool = False,
                 expiry_buffer_seconds: int = 60,
                 timeout: Tuple[float, float] = (5.0, 30.0)):
        """
        Initialize Kiwoom OAuth client.

        Args:
            appkey: Application key from Kiwoom
            secretkey: Secret key from Kiwoom
            use_mock: Whether to use mock trading environment (default: False)
            expiry_buffer_seconds: 만료 이 초 전부터 미리 갱신. 주문 도중
                토큰이 만료되어 실패하는 것을 막습니다.
            timeout: (connect, read) 타임아웃
        """
        self.appkey = appkey
        self.secretkey = secretkey
        self.use_mock = use_mock
        self.base_url = self.MOCK_BASE_URL if use_mock else self.PROD_BASE_URL
        self.expiry_buffer_seconds = expiry_buffer_seconds
        self.timeout = timeout
        self.token: Optional[str] = None
        self.token_expires: Optional[datetime] = None
        self.token_type: Optional[str] = None

    def authenticate(self) -> Dict[str, Any]:
        """
        Authenticate with Kiwoom OAuth server and obtain access token.
        
        Returns:
            Dict containing authentication response
            
        Raises:
            requests.RequestException: If authentication request fails
            ValueError: If response format is invalid
        """
        url = f"{self.base_url}{self.TOKEN_ENDPOINT}"
        
        headers = {
            "Content-Type": "application/json;charset=UTF-8"
        }
        
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.appkey,
            "secretkey": self.secretkey
        }
        
        try:
            response = requests.post(url, json=payload, headers=headers,
                                     timeout=self.timeout)
            response.raise_for_status()
            
            data = response.json()
            
            # Store authentication details
            self.token = data.get("token")
            self.token_type = data.get("token_type", "bearer")
            
            # Parse expiration date
            expires_dt_str = data.get("expires_dt")
            if expires_dt_str:
                self.token_expires = datetime.strptime(expires_dt_str, "%Y%m%d%H%M%S")
            
            return data
            
        except requests.RequestException as e:
            raise requests.RequestException(f"OAuth authentication failed: {str(e)}")
        except (KeyError, ValueError) as e:
            raise ValueError(f"Invalid response format: {str(e)}")
    
    def is_token_valid(self) -> bool:
        """
        Check if current token is valid and not about to expire.

        만료 직전(expiry_buffer_seconds 이내)이면 무효로 간주해 미리 갱신합니다.

        Returns:
            True if token is valid, False otherwise
        """
        if not self.token or not self.token_expires:
            return False

        deadline = self.token_expires - timedelta(seconds=self.expiry_buffer_seconds)
        return datetime.now() < deadline
    
    def get_auth_header(self) -> Dict[str, str]:
        """
        Get authorization header for API requests.
        
        Returns:
            Dict with authorization header
            
        Raises:
            ValueError: If token is not available or expired
        """
        if not self.is_token_valid():
            raise ValueError("Token is not valid or expired. Please authenticate first.")
        
        return {
            "Authorization": f"{self.token_type} {self.token}"
        }
    
    def refresh_token_if_needed(self) -> bool:
        """
        Refresh token if it's expired or about to expire.
        
        Returns:
            True if token was refreshed, False if still valid
        """
        if self.is_token_valid():
            return False
        
        self.authenticate()
        return True


def main():
    """Example usage of KiwoomOAuth module."""
    # Example credentials (replace with actual values)
    appkey = "your_app_key_here"
    secretkey = "your_secret_key_here"
    
    # Create OAuth client
    oauth = KiwoomOAuth(appkey=appkey, secretkey=secretkey, use_mock=True)
    
    try:
        # Authenticate
        print("Authenticating with Kiwoom OAuth...")
        auth_response = oauth.authenticate()
        print(f"Authentication successful!")
        print(f"Token type: {oauth.token_type}")
        print(f"Token expires at: {oauth.token_expires}")
        
        # Check if token is valid
        if oauth.is_token_valid():
            print("Token is valid")
            
            # Get authorization header for API requests
            auth_header = oauth.get_auth_header()
            print(f"Authorization header: {auth_header}")
        
    except Exception as e:
        print(f"Error: {str(e)}")


if __name__ == "__main__":
    main()