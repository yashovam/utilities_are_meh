import cv2
import numpy as np
import RPi.GPIO as GPIO

# Set up GPIO for switch control
GPIO.setmode(GPIO.BCM)
GPIO.setup(17, GPIO.OUT)

# Initialize the camera
cap = cv2.VideoCapture(0)

while True:


    
    # Read frame from camera
    ret, frame = cap.read()
    if not ret:
        break

    # Convert to HSV color space
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    # Define yellow color range in HSV
    lower_yellow = np.array([15, 50, 50])
    upper_yellow = np.array([30, 255, 255])

    # Create a mask for yellow objects
    mask = cv2.inRange(hsv, lower_yellow, upper_yellow)

    # Find contours of yellow regions
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Check if any contour is found
    max_area = 0
    if len(cnts) > 0:
        for c in cnts:
            area = cv2.contourArea(c)
            if area > max_area:
                max_area = area

    # Trigger switch when a significant yellow object (flame) is detected
    if max_area > 100:  # Adjust threshold based on testing
        print("Flame detected!")
        GPIO.output(17, GPIO.HIGH)
        cv2.waitKey(500)  # Wait for half a second to prevent repeated triggers

    # Display the frame
    cv2.imshow('Frame', frame)

    # Exit if ESC pressed
    if cv2.waitKey(1) == ord('q'):
        break

# Release resources
cap.release()
GPIO.cleanup()
cv2.destroyAllWindows()
