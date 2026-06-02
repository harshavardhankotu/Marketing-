import time
import random
import concurrent.futures

class TimeoutException(Exception):
    pass

def timeout_call(timeout_sec, func, *args, **kwargs):
    """
    Executes a callable inside a thread pool with a strict timeout.
    Windows-safe timeout mechanism (avoids signal.SIGALRM).
    """
    if timeout_sec is None or timeout_sec <= 0:
        return func(*args, **kwargs)
        
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError:
            raise TimeoutException(f"Operation timed out after {timeout_sec} seconds.")

def call_with_retry(func, *args, max_retries=3, base_delay=1.0, max_delay=10.0, exceptions=(Exception,), provider="default", **kwargs):
    """
    Executes a callable with exponential backoff and randomized jitter.
    Returns a structured object:
      {
         "success": bool,
         "result": Any,
         "error": str,
         "error_class": str,
         "attempts": int
      }
    """
    attempts = 0
    last_error = None
    
    while attempts < max_retries:
        attempts += 1
        try:
            res = func(*args, **kwargs)
            return {
                "success": True,
                "result": res,
                "error": None,
                "error_class": None,
                "attempts": attempts
            }
        except exceptions as e:
            last_error = e
            print(f"[RETRY_LOGGER] Provider '{provider}' attempt {attempts}/{max_retries} failed: {e}")
            if attempts < max_retries:
                # Delay = min(max_delay, base_delay * (2 ^ (attempt - 1))) + jitter
                delay = min(max_delay, base_delay * (2 ** (attempts - 1))) + random.uniform(0, 0.5)
                time.sleep(delay)
                
    # Classify error
    err_str = str(last_error)
    error_class = "CRITICAL"
    if "rate limit" in err_str.lower() or "429" in err_str.lower():
        error_class = "RATE_LIMIT"
    elif "timeout" in err_str.lower():
        error_class = "TIMEOUT"
    elif "authentication" in err_str.lower() or "unauthorized" in err_str.lower() or "api key" in err_str.lower():
        error_class = "AUTH_ERROR"
        
    return {
        "success": False,
        "result": None,
        "error": err_str,
        "error_class": error_class,
        "attempts": attempts
    }
