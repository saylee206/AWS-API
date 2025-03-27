from fastapi import FastAPI, HTTPException
import boto3
import pandas as pd
from datetime import datetime
import os
import logging
import time
from typing import List, Dict, Optional, Any, Union
#uvicorn main:app --reload

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('aws_inventory.log')
    ]
)
logger = logging.getLogger(__name__)


# Create FastAPI app
app = FastAPI(title="AWS Asset Inventory Connector")

class HardwareSpecs:
    #Hardware specification constants
    # Memory map for EC2 instance types (with more detailed information)
    INSTANCE_MEMORY = {
        "t2.micro": "1 GiB",
        "t2.small": "2 GiB",
        "t2.medium": "4 GiB",
        "t2.large": "8 GiB",
        "m5.large": "8 GiB",
        "m5.xlarge": "16 GiB",
        "c5.large": "4 GiB",
    }


class AWSClient:
    #Class to handle AWS client connections and operations
    
    def __init__(self):
        logger.info("Initializing AWSClient")
        self.ec2 = None
        self.ssm = None
        self.initialize_clients()
    
    def initialize_clients(self):
        logger.info("Attempting to initialize AWS EC2 and SSM clients")
        try:
            self.ec2 = boto3.client("ec2")
            self.ssm = boto3.client("ssm")
            logger.info("Successfully initialized AWS clients")
        except Exception as e:
            logger.error(f"Failed to initialize AWS clients: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"AWS configuration error: {str(e)}")
    
        
    
    def get_instances(self) -> List[Dict]:
        logger.info("Fetching all EC2 instances")
        try:
            response = self.ec2.describe_instances()
            instances = []
            
            for reservation in response["Reservations"]:
                for instance in reservation["Instances"]:
                    instances.append({
                        "InstanceId": instance["InstanceId"],
                        "InstanceType": instance["InstanceType"],
                        "State": instance["State"]["Name"],
                        "PublicIP": instance.get("PublicIpAddress", "N/A"),
                        "PrivateIP": instance.get("PrivateIpAddress", "N/A"),
                        "Platform": instance.get("Platform", "Linux/UNIX"),
                        "LaunchTime": instance.get("LaunchTime", "").isoformat() if "LaunchTime" in instance else "",
                    })
            
            logger.info(f"Successfully retrieved {len(instances)} EC2 instances")
            return instances
        except Exception as e:
            logger.error(f"Failed to get EC2 instances: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
        
    
    def get_instance_details(self, instance_id: str) -> Dict:
        #Get detailed information for a specific EC2 instance
        try:
            response = self.ec2.describe_instances(InstanceIds=[instance_id])
            
            if not response["Reservations"] or not response["Reservations"][0]["Instances"]:
                raise HTTPException(status_code=404, detail=f"Instance {instance_id} not found")
                
            return response["Reservations"][0]["Instances"][0]
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting instance details: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
    
    def get_managed_instances(self) -> List[str]:
        #Get list of SSM managed instance IDs
        try:
            managed_instances = self.ssm.describe_instance_information()
            return [instance["InstanceId"] for instance in managed_instances.get("InstanceInformationList", [])]
        except Exception as e:
            logger.warning(f"Could not get SSM managed instances: {str(e)}")
            return []
    
    def get_disk_size(self, instance_id: str) -> str:
        #Get total disk size for an instance
        try:
            # Get block device mappings
            response = self.ec2.describe_instances(InstanceIds=[instance_id])
            
            if not response["Reservations"] or not response["Reservations"][0]["Instances"]:
                return "N/A"
                
            instance = response["Reservations"][0]["Instances"][0]
            
            # Get volume IDs
            volume_ids = []
            for device in instance.get("BlockDeviceMappings", []):
                if "Ebs" in device:
                    volume_ids.append(device["Ebs"]["VolumeId"])
            
            # Get volume sizes
            total_size = 0
            for volume_id in volume_ids:
                volume = self.ec2.describe_volumes(VolumeIds=[volume_id])
                if volume and "Volumes" in volume and volume["Volumes"]:
                    total_size += volume["Volumes"][0]["Size"]
            
            return f"{total_size} GB"
        except Exception as e:
            logger.warning(f"Could not get disk size for {instance_id}: {str(e)}")
            return "N/A"
    
    def get_instance_hostname(self, instance_id: str) -> str:
        #Get hostname using SSM command execution
        try:
            # For Windows instances
            windows_command = "hostname"
            # For Linux instances
            linux_command = "hostname"
            
            # Try Windows command first
            response = self.ssm.send_command(
                InstanceIds=[instance_id],
                DocumentName="AWS-RunPowerShellScript",
                Parameters={"commands": [windows_command]}
            )
            
            command_id = response["Command"]["CommandId"]
            
            # Wait for command to complete
            time.sleep(2)
            
            # Get command output
            output = self.ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance_id
            )
            
            if output["Status"] == "Success":
                return output["StandardOutputContent"].strip()
            
            # If Windows command fails, try Linux command
            response = self.ssm.send_command(
                InstanceIds=[instance_id],
                DocumentName="AWS-RunShellScript",
                Parameters={"commands": [linux_command]}
            )
            
            command_id = response["Command"]["CommandId"]
            
            # Wait for command to complete
            time.sleep(2)
            
            # Get command output
            output = self.ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance_id
            )
            
            if output["Status"] == "Success":
                return output["StandardOutputContent"].strip()
            
            return "N/A"
        except Exception as e:
            logger.warning(f"Could not get hostname for {instance_id}: {str(e)}")
            return "N/A"
    
    def get_instance_serial(self, instance_id: str) -> str:
        #Get instance serial number/system UUID
        try:
            # Windows command to get serial number
            windows_command = "(Get-WmiObject -Class Win32_ComputerSystemProduct).UUID"
            # Linux command to get system UUID
            linux_command = "cat /sys/class/dmi/id/product_uuid || dmidecode -s system-uuid || echo N/A"
            
            # Try Windows command first
            response = self.ssm.send_command(
                InstanceIds=[instance_id],
                DocumentName="AWS-RunPowerShellScript",
                Parameters={"commands": [windows_command]}
            )
            
            command_id = response["Command"]["CommandId"]
            
            # Wait for command to complete
            time.sleep(2)
            
            # Get command output
            output = self.ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance_id
            )
            
            if output["Status"] == "Success" and output["StandardOutputContent"].strip():
                return output["StandardOutputContent"].strip()
            
            # If Windows command fails, try Linux command
            response = self.ssm.send_command(
                InstanceIds=[instance_id],
                DocumentName="AWS-RunShellScript",
                Parameters={"commands": [linux_command]}
            )
            
            command_id = response["Command"]["CommandId"]
            
            # Wait for command to complete
            time.sleep(2)
            
            # Get command output
            output = self.ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance_id
            )
            
            if output["Status"] == "Success" and output["StandardOutputContent"].strip():
                return output["StandardOutputContent"].strip()
            
            # If both fail, use instance ID as fallback
            return f"i-{instance_id[-8:]}"  # Use last 8 chars of instance ID
        except Exception as e:
            logger.warning(f"Could not get serial for {instance_id}: {str(e)}")
            return f"i-{instance_id[-8:]}"  # Fallback
    
    def get_software_inventory(self, instance_id: str) -> Dict:
        #Get software inventory for an instance
        try:
            # Get software inventory from SSM
            response = self.ssm.list_inventory_entries(
                InstanceId=instance_id,
                TypeName="AWS:Application"
            )
            
            return response.get("Entries", [])
        except Exception as e:
            logger.warning(f"Could not get software inventory for {instance_id}: {str(e)}")
            return []


class InstanceData:
    #Class representing EC2 instance data
    
    def __init__(self, instance_data: Dict):
        self.instance_id = instance_data["InstanceId"]
        self.instance_type = instance_data["InstanceType"]
        self.state = instance_data["State"]["Name"]
        self.public_ip = instance_data.get("PublicIpAddress", "N/A")
        self.private_ip = instance_data.get("PrivateIpAddress", "N/A")
        self.platform = instance_data.get("Platform", "Linux/UNIX")
        self.launch_time = instance_data.get("LaunchTime", "")
        self.vpc_id = instance_data.get("VpcId", "N/A")
        self.cpu_cores = instance_data["CpuOptions"].get("CoreCount", 1)
        self.threads_per_core = instance_data["CpuOptions"].get("ThreadsPerCore", 1)
        self.total_vcpus = self.cpu_cores * self.threads_per_core
        self.security_groups = [sg["GroupName"] for sg in instance_data.get("SecurityGroups", [])]
        self.block_devices = instance_data.get("BlockDeviceMappings", [])
        self.tags = instance_data.get("Tags", [])
        self.name = self._get_name_from_tags()
        self.memory = HardwareSpecs.INSTANCE_MEMORY.get(self.instance_type, "Unknown")
    
    def _get_name_from_tags(self) -> str:
        #Extract Name tag from instance tags
        for tag in self.tags:
            if tag["Key"] == "Name":
                return tag["Value"]
        return "N/A"
    
    def to_dict(self) -> Dict:
        #Convert instance data to dictionary
        return {
            "InstanceId": self.instance_id,
            "InstanceType": self.instance_type,
            "State": self.state,
            "PublicIP": self.public_ip,
            "PrivateIP": self.private_ip,
            "Platform": self.platform,
            "LaunchTime": self.launch_time.isoformat() if self.launch_time else "",
            "Name": self.name
        }


class HardwareService:
    #Service class for hardware operations
    
    def __init__(self, aws_client: AWSClient):
        logger.info("Initializing HardwareService")
        self.aws_client = aws_client
        
    
    def get_hardware_info(self, instance_id: str) -> Dict:
        logger.info(f"Fetching hardware information for instance {instance_id}")
        #Get hardware information for an instance
        # Get instance details
        instance_data = self.aws_client.get_instance_details(instance_id)
        instance = InstanceData(instance_data)
        logger.debug(f"Processing storage devices for instance {instance_id}")
        
        # Get storage devices and total size
        storage_devices = []
        total_storage_gb = 0
        
        for device in instance.block_devices:
            if "Ebs" in device:
                volume_id = device["Ebs"]["VolumeId"]
                volume = self.aws_client.ec2.describe_volumes(VolumeIds=[volume_id])
                
                if volume and "Volumes" in volume and volume["Volumes"]:
                    volume_size = volume["Volumes"][0]["Size"]
                    total_storage_gb += volume_size
                    
                    storage_devices.append({
                        "DeviceName": device["DeviceName"],
                        "VolumeId": volume_id,
                        "SizeGB": volume_size
                    })
        
        # Get hostname from instance tags or SSM
        hostname = instance.name  # Default to instance name
        
        # Check if instance is managed by SSM
        managed_instances = self.aws_client.get_managed_instances()
        
        # If managed by SSM, try to get hostname and serial
        serial_number = f"i-{instance_id[-8:]}"  # Default
        if instance_id in managed_instances:
            # Get hostname
            ssm_hostname = self.aws_client.get_instance_hostname(instance_id)
            if ssm_hostname != "N/A":
                hostname = ssm_hostname
            
            # Get serial number
            ssm_serial = self.aws_client.get_instance_serial(instance_id)
            if ssm_serial != "N/A":
                serial_number = ssm_serial
        
        return {
            "InstanceId": instance_id,
            "HostName": hostname,
            "HostType": instance.instance_type,
            "SerialNumber": serial_number,
            "CPU": {
                "Cores": instance.cpu_cores,
                "ThreadsPerCore": instance.threads_per_core,
                "TotalvCPUs": instance.total_vcpus
            },
            "Memory": instance.memory,
            "Storage": {
                "Devices": storage_devices,
                "TotalSizeGB": total_storage_gb
            },
            "NetworkInterfaces": len(instance_data.get("NetworkInterfaces", [])),
            "SecurityGroups": instance.security_groups
        }
    
    def export_hardware_data(self) -> Dict:
        #Export hardware information for all instances
        instances = self.aws_client.get_instances()
        managed_instances = self.aws_client.get_managed_instances()
        hardware_data = []
        
        for instance_info in instances:
            instance_id = instance_info["InstanceId"]
            
            # Get instance details
            try:
                instance_data = self.aws_client.get_instance_details(instance_id)
                instance = InstanceData(instance_data)
                
                # Get disk size
                total_disk_size = "N/A"
                volume_ids = []
                for device in instance.block_devices:
                    if "Ebs" in device:
                        volume_ids.append(device["Ebs"]["VolumeId"])
                
                total_size = 0
                for volume_id in volume_ids:
                    try:
                        volume = self.aws_client.ec2.describe_volumes(VolumeIds=[volume_id])
                        if volume and "Volumes" in volume and volume["Volumes"]:
                            total_size += volume["Volumes"][0]["Size"]
                    except Exception as e:
                        logger.warning(f"Could not get volume size for {volume_id}: {str(e)}")
                
                if total_size > 0:
                    total_disk_size = f"{total_size} GB"
                
                # Get hostname and serial number
                hostname = instance.name
                serial_number = f"i-{instance_id[-8:]}"  # Default
                
                if instance_id in managed_instances:
                    # Get hostname
                    ssm_hostname = self.aws_client.get_instance_hostname(instance_id)
                    if ssm_hostname != "N/A":
                        hostname = ssm_hostname
                    
                    # Get serial number
                    ssm_serial = self.aws_client.get_instance_serial(instance_id)
                    if ssm_serial != "N/A":
                        serial_number = ssm_serial
                
                hardware_data.append({
                    "InstanceId": instance_id,
                    "Name": instance.name,
                    "HostName": hostname,
                    "HostType": instance.instance_type,
                    "SerialNumber": serial_number,
                    "State": instance.state,
                    "CPUCores": instance.cpu_cores,
                    "ThreadsPerCore": instance.threads_per_core,
                    "TotalVCPUs": instance.total_vcpus,
                    "RAM": instance.memory,
                    "HardDiskSize": total_disk_size,
                    "PublicIP": instance.public_ip,
                    "PrivateIP": instance.private_ip,
                    "VPC": instance.vpc_id,
                    "Platform": instance.platform,
                })
            except Exception as e:
                logger.warning(f"Error processing hardware data for {instance_id}: {str(e)}")
        
        # Create filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"aws_hardware_{timestamp}.csv"
        
        # Create CSV
        df = pd.DataFrame(hardware_data)
        df.to_csv(filename, index=False)
        
        return {
            "message": f"Hardware data exported to {filename}",
            "records": len(hardware_data),
            "file_path": os.path.abspath(filename)
        }


class SoftwareService:
    #Service class for software operations
    
    def __init__(self, aws_client: AWSClient):
        logger.info("Initializing SoftwareService")
        self.aws_client = aws_client
    
    def get_software_info(self, instance_id: str) -> Dict:
        #Get software information for an instance
        # Check if instance is managed by SSM
        logger.info(f"Fetching software information for instance {instance_id}")
        managed_instances = self.aws_client.get_managed_instances()
        
        if instance_id not in managed_instances:
            logger.warning(f"Instance {instance_id} is not managed by SSM")
            return {
                "instance_id": instance_id,
                "status": "Not managed by SSM",
                "note": "This instance does not have SSM Agent installed",
                "software": []
            }
        
        try:
            # Get software inventory
            software_list = self.aws_client.get_software_inventory(instance_id)
            
            # Format software data
            formatted_software = []
            for app in software_list:
                formatted_software.append({
                    "Name": app.get("Name", "Unknown"),
                    "Version": app.get("Version", "Unknown"),
                    "Publisher": app.get("Publisher", "Unknown"),
                    "InstalledTime": app.get("InstalledTime", "Unknown"),
                })
                
            return {
                "instance_id": instance_id,
                "status": "Success",
                "software_count": len(formatted_software),
                "software": formatted_software
            }
        except Exception as e:
            error_msg = str(e)
            if "InvalidInstanceId" in error_msg:
                return {
                    "instance_id": instance_id,
                    "status": "Instance not found or not configured with SSM",
                    "software": []
                }
            else:
                logger.error(f"Error getting software: {error_msg}")
                return {
                    "instance_id": instance_id,
                    "status": "Error",
                    "error": error_msg,
                    "software": []
                }
    
    def export_software_data(self) -> Dict:
        #Export software information for all instances
        # Get instance details
        instance_data_map = {}
        instances = self.aws_client.get_instances()
        
        for instance_info in instances:
            instance_id = instance_info["InstanceId"]
            try:
                instance_data = self.aws_client.get_instance_details(instance_id)
                instance = InstanceData(instance_data)
                
                hostname = instance.name
                # Try to get hostname from SSM if possible
                try:
                    ssm_hostname = self.aws_client.get_instance_hostname(instance_id)
                    if ssm_hostname != "N/A":
                        hostname = ssm_hostname
                except Exception:
                    pass
                
                instance_data_map[instance_id] = {
                    "Name": instance.name,
                    "HostName": hostname,
                    "InstanceType": instance.instance_type,
                    "State": instance.state
                }
            except Exception as e:
                logger.warning(f"Error getting instance details for {instance_id}: {str(e)}")
                instance_data_map[instance_id] = {
                    "Name": "Unknown",
                    "HostName": "Unknown",
                    "InstanceType": "Unknown",
                    "State": "Unknown"
                }
        
        # Get SSM managed instances
        managed_instances = self.aws_client.get_managed_instances()
        software_data = []
        
        # For each SSM managed instance, get software
        for instance_id in managed_instances:
            try:
                # Get instance info
                instance_info = instance_data_map.get(instance_id, {
                    "Name": "Unknown",
                    "HostName": "Unknown",
                    "InstanceType": "Unknown",
                    "State": "Unknown"
                })
                
                # Get software inventory
                apps = self.aws_client.get_software_inventory(instance_id)
                
                # Add each application as a row
                if apps:
                    for app in apps:
                        software_data.append({
                            "InstanceId": instance_id,
                            "InstanceName": instance_info["Name"],
                            "HostName": instance_info["HostName"],
                            "HostType": instance_info["InstanceType"],
                            "ApplicationName": app.get("Name", "Unknown"),
                            "Version": app.get("Version", "Unknown"),
                            "Publisher": app.get("Publisher", "Unknown"),
                            "InstalledTime": app.get("InstalledTime", "Unknown"),
                        })
                else:
                    # No applications found
                    software_data.append({
                        "InstanceId": instance_id,
                        "InstanceName": instance_info["Name"],
                        "HostName": instance_info["HostName"],
                        "HostType": instance_info["InstanceType"],
                        "ApplicationName": "No applications found",
                        "Version": "N/A",
                        "Publisher": "N/A",
                        "InstalledTime": "N/A",
                    })
            
            except Exception as e:
                logger.warning(f"Error getting software for {instance_id}: {str(e)}")
        
        # Create filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"aws_software_{timestamp}.csv"
        
        # Create CSV
        df = pd.DataFrame(software_data)
        df.to_csv(filename, index=False)
        
        return {
            "message": f"Software data exported to {filename}",
            "records": len(software_data),
            "ssm_managed_instances": len(managed_instances),
            "file_path": os.path.abspath(filename)
        }


class AssetInventoryService:
    #Main service class for the asset inventory application
    
    def __init__(self):
        self.aws_client = AWSClient()
        self.hardware_service = HardwareService(self.aws_client)
        self.software_service = SoftwareService(self.aws_client)
    
    def get_instances(self) -> Dict:
        #Get all EC2 instances#
        instances = self.aws_client.get_instances()
        return {"instances": instances, "count": len(instances)}
    
    def get_hardware_info(self, instance_id: str) -> Dict:
        #Get hardware information for an instance
        return self.hardware_service.get_hardware_info(instance_id)
    
    def get_software_info(self, instance_id: str) -> Dict:
        #Get software information for an instance
        return self.software_service.get_software_info(instance_id)
    
    def export_hardware_data(self) -> Dict:
        #Export hardware information for all instances
        return self.hardware_service.export_hardware_data()
    
    def export_software_data(self) -> Dict:
        #Export software information for all instances
        return self.software_service.export_software_data()
    
    def export_all_data(self) -> Dict:
        #Export both hardware and software data
        try:
            # Export hardware
            hardware_result = self.export_hardware_data()
            
            # Export software
            software_result = self.export_software_data()
            
            return {
                "message": "All inventory data exported successfully",
                "hardware_file": hardware_result["file_path"],
                "hardware_records": hardware_result["records"],
                "software_file": software_result["file_path"],
                "software_records": software_result["records"],
            }
        
        except Exception as e:
            logger.error(f"Error exporting all data: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))


# Initialize the service
asset_inventory_service = AssetInventoryService()

# API Routes
@app.get("/")
def home():
    logger.info("Home endpoint accessed")
    #Homepage with API information
    return {
        "message": "AWS Asset Inventory Connector",
        "endpoints": [
            "/instances", 
            "/hardware/{instance_id}", 
            "/software/{instance_id}", 
            "/export_hardware",
            "/export_software", 
            "/export_all"
        ]
    }

@app.get("/instances")
def get_instances():
    #List of all EC2 instances
    logger.info("Handling request to get all instances")
    return asset_inventory_service.get_instances()

@app.get("/hardware/{instance_id}")
def get_hardware(instance_id: str):
    #Hardware details for a specific EC2 instance
    logger.info(f"Handling request to get hardware for instance {instance_id}")
    return asset_inventory_service.get_hardware_info(instance_id)

@app.get("/software/{instance_id}")
def get_software(instance_id: str):
    #Software details for a specific EC2 instance
    logger.info(f"Handling request to get software for instance {instance_id}")
    return asset_inventory_service.get_software_info(instance_id)

@app.get("/export_hardware")
def export_hardware_to_csv():
    #Export hardware information to CSV
    logger.info("Handling request to export hardware data")
    return asset_inventory_service.export_hardware_data()

@app.get("/export_software")
def export_software_to_csv():
    #Export software information to CSV
    logger.info("Handling request to export software data")
    return asset_inventory_service.export_software_data()

@app.get("/export_all")
def export_all_to_csv():
    #Export both hardware and software to CSV
    logger.info("Handling request to export all data")
    return asset_inventory_service.export_all_data()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)