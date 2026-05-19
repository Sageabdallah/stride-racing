#!/usr/bin/env python3
"""ML Model Status Script — returns the current status of the trained ML model."""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(__file__))

def main():
    try:
        from ml_model import RacingMLModel
        
        model = RacingMLModel()
        status = model.get_status()
        
        print(json.dumps(status))
        
    except Exception as e:
        print(json.dumps({
            'is_trained': False,
            'error': str(e)
        }))


if __name__ == '__main__':
    main()
