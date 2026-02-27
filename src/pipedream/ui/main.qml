import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    visible: true
    width: 1280
    height: 720
    title: "PipeDream // Visualizer"
    color: "#1e1e1e"

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // MAIN CONTENT AREA
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            // LEFT: Visualizer
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
                
                BusyIndicator {
                    anchors.centerIn: parent
                    running: backend.status_message !== "Ready" && backend.status_message !== "Error"
                    visible: running
                }
            }

            // RIGHT: Terminal
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
                            
                            onTextChanged: { cursorPosition = length }
                        }
                    }

                    Rectangle { Layout.fillWidth: true; height: 1; color: "#333" }

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

        // STATUS BAR
        Rectangle {
            Layout.fillWidth: true
            height: 30
            color: "#252525"
            
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 10
                anchors.rightMargin: 10

                Text {
                    text: backend.status_message
                    color: "#aaa"
                    font.pixelSize: 12
                }

                Item { Layout.fillWidth: true } // Spacer

                Text {
                    text: "SESSION COST: $" + backend.session_cost
                    color: "#00ff00"
                    font.family: "Courier New"
                    font.pixelSize: 12
                    font.bold: true
                }
            }
        }
    }
}