import cv2
import os
import time

def viewer_process(image_queue):
    """
    Independent process that monitors a queue for image paths 
    and updates an OpenCV window.
    """
    window_name = "PipeDream Visualizer"
    
    # Create the window initially so it pops up
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, 600, 600)
    
    # Create a default "Loading" or black screen if you want, 
    # but for now we just wait for the first image.
    print("[Viewer] Window initialized. Waiting for images...")

    running = True
    while running:
        # Check for new images in the queue (non-blocking)
        while not image_queue.empty():
            data = image_queue.get()
            
            # Poison Pill: If Engine sends None, we quit
            if data is None:
                running = False
                break
            
            # Load and display the image
            if os.path.exists(data):
                img = cv2.imread(data)
                if img is not None:
                    cv2.imshow(window_name, img)
                else:
                    print(f"[Viewer] Failed to load image: {data}")

        # OpenCV GUI magic: waitKey handles the event loop
        # We wait 100ms to allow the window to refresh
        key = cv2.waitKey(100)
        
        # Allow user to close window manually to kill viewer (optional)
        try:
            if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                running = False
        except Exception:
            pass

    cv2.destroyAllWindows()
    print("[Viewer] Shutting down.")