"""Weather API stub for race day going condition updates. Current implementation returns static fallback; replace with BOM API or Weatherstack AU when credentials are available."""

from typing import Optional, Dict, Any
from datetime import datetime


# Tracks that change going classification quickly after rain
FAST_DRAINING_TRACKS = ['flemington', 'randwick', 'caulfield']
SLOW_DRAINING_TRACKS = ['eagle farm', 'doomben', 'gold coast']


class WeatherAPIError(Exception):
    pass


def get_projected_going(
    track_name: str,
    race_date: str,
    race_time_local: Optional[str] = None,
    current_going: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetch projected going condition for a track on race day.

    Returns:
        Dict with projected_going, confidence, rain_probability, temp_celsius, source, last_updated
    """
    # --- STUB IMPLEMENTATION ---
    # Replace with real BOM API or Weatherstack AU calls when credentials available
    fallback_going = current_going or 'Good'

    return {
        'projected_going': fallback_going,
        'confidence': 0.0,
        'rain_probability': None,
        'temp_celsius': None,
        'source': 'stub',
        'last_updated': datetime.utcnow().isoformat(),
    }


def should_widen_going_uncertainty(weather_result: Dict[str, Any]) -> bool:
    """Returns True if conditions suggest going uncertainty should be widened."""
    confidence = weather_result.get('confidence', 0.0)
    rain_prob = weather_result.get('rain_probability')

    if confidence < 0.3:
        return True
    if rain_prob is not None and rain_prob > 0.4:
        return True
    return False


def get_going_uncertainty_boost(weather_result: Dict[str, Any]) -> float:
    """
    Returns a sigma multiplier boost (>= 1.0) for weather-uncertain going.
    Applied on top of base going profile sigma multipliers.
    """
    if not should_widen_going_uncertainty(weather_result):
        return 1.0

    rain_prob = weather_result.get('rain_probability', 0.0) or 0.0
    confidence = weather_result.get('confidence', 0.0)

    uncertainty_boost = 1.0 + (1.0 - confidence) * 0.08 + rain_prob * 0.07
    return min(1.15, uncertainty_boost)
