# SeaBirdGuard
Intelligent Offshore Bird Detection and Repellent System
SeaBirdGuard is an intelligent offshore bird detection and repellent system designed for marine facilities, aquaculture farms, offshore platforms, ports, and coastal infrastructure. The system combines computer vision, automated bird deterrence, and remote fleet management to reduce bird-related interference and equipment damage.
Project Overview
SeaBirdGuard uses an industrial camera to continuously monitor the sea surface and surrounding airspace. Captured images are transmitted to a Linux or Windows-based edge computer for real-time processing.
A YOLO-based bird detection model analyzes video streams at configurable frame intervals (default: every 20 frames) to balance detection performance and hardware resource consumption.
Once birds are detected, the system automatically activates deterrent devices, including:
Laser bird repellent module
Bird-discomfort acoustic signals
Propane-air ignition cannon for loud deterrent blasts
The system supports both standalone deployment and large-scale cluster deployment for offshore environments.
System Architecture
Industrial Camera (MindVision) ↓ Image Acquisition ↓ Linux / Windows Edge Computer ↓ YOLO-based Bird Detection ↓ Decision Engine ↓ Bird Repellent Devices
Laser Module
Acoustic Module
Propane Cannon Module
↓
Remote Management Platform
Hardware Components
Vision System
Industrial Camera: MindVision Camera
Adjustable frame sampling strategy
Edge computing deployment
Repellent System
Laser Repellent
A directional laser beam is activated when birds enter predefined monitoring zones.
Acoustic Repellent
Bird-specific audio frequencies and warning sounds are played to create an uncomfortable environment for birds.
Propane Cannon
A propane-air mixture is ignited to generate a loud cannon-like sound, effectively dispersing bird flocks over large areas.
AI Detection Module
Detection Framework
YOLO-based object detection
Real-time bird recognition
Multi-class bird target support
Configurable confidence threshold
Frame Sampling Strategy
The system processes one frame every 20 frames by default.
Parameters can be adjusted according to:
CPU performance
GPU capability
Required response speed
Camera frame rate
This approach significantly reduces computational load while maintaining reliable detection accuracy.
Remote Operation Platform
SeaBirdGuard integrates with OpenClaw and QBot to provide intelligent remote operation and maintenance capabilities.
Supported communication platforms:
QQ
WeChat
Feishu (Lark)
Users can remotely:
Check device status
View operation logs
Monitor bird detection events
Start or stop deterrent modules
Adjust system parameters
Receive alarm notifications
Cluster Deployment
For large offshore facilities, multiple SeaBirdGuard units can be deployed as a distributed fleet.
Cluster management features include:
Centralized monitoring
Unified configuration management
Remote device grouping
Batch command execution
Fleet status visualization
Distributed log collection
This architecture enables efficient management of hundreds of devices across large marine environments.
Key Features
Real-time offshore bird detection
YOLO-based computer vision
Configurable frame processing strategy
Laser deterrent system
Acoustic deterrent system
Propane cannon deterrent system
Linux edge deployment
Remote operation via QQ, WeChat, and Feishu
OpenClaw + QBot integration
Cluster deployment support
Centralized device management
Future Work
Bird species classification
Bird behavior prediction
Thermal camera integration
Multi-camera fusion
AI-assisted deterrent strategy optimization
Cloud-based monitoring dashboard
License
This project is intended for academic research, intelligent marine infrastructure applications, and bird hazard prevention studies.
