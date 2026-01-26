import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    visible: true
    width: 1280
    height: 720
    title: "PipeDream // Visualizer"
    color: "#1e1e1e"

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // LEFT: The Visualizer
        Rectangle {
            Layout.fillHeight: true
            Layout.preferredWidth: parent.width * 0.5
            color: "#000000"

            Image {
                id: sceneImage
                anchors.fill: parent
                fillMode: Image.PreserveAspectFit
                source: backend.current_image
                
                Behavior on source {
                    SequentialAnimation {
                        NumberAnimation { target: sceneImage; property: "opacity"; to: 0; duration: 200 }
                        PropertyAction { target: sceneImage; property: "source" }
                        NumberAnimation { target: sceneImage; property: "opacity"; to: 1; duration: 500 }
                    }
                }
            }
            
            Text {
                anchors.centerIn: parent
                text: "WAITING FOR SIGNAL..."
                color: "#444"
                font.pixelSize: 24
                visible: sceneImage.status !== Image.Ready
            }
        }

        // RIGHT: The Terminal
        Rectangle {
            Layout.fillHeight: true
            Layout.fillWidth: true
            color: "#0c0c0c"
            
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 10

                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    
                    TextArea {
                        id: gameOutput
                        readOnly: true
                        color: "#00ff00"
                        font.family: "Courier New"
                        font.pixelSize: 14
                        background: null
                        wrapMode: Text.WordWrap
                        text: backend.console_text
                        
                        // Auto-scroll to bottom
                        onTextChanged: {
                            cursorPosition = length
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    height: 1
                    color: "#333"
                }

                TextField {
                    id: inputField
                    Layout.fillWidth: true
                    placeholderText: "Enter command..."
                    color: "white"
                    font.family: "Courier New"
                    font.pixelSize: 14
                    background: null
                    
                    onAccepted: {
                        if (text.trim() !== "") {
                            backend.send_command(text)
                            text = ""
                        }
                    }
                }
            }
        }
    }
}